"""Tests for scripts/affected_tests.py (run: python3 -m unittest discover -s scripts -p 'test_*.py')."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import affected_tests as at  # noqa: E402

PY = "backend/python/"
NODE = "backend/nodejs/apps/"

COMPONENTS = [
    {"id": "JRN", "depends": [], "source": [NODE + "src/modules/agent-harness/journal/**"],
     "tests": [NODE + "tests/modules/agent-harness/journal-*.ts", PY + "tests/unit/harness/journal/**",
               PY + "tests/cohesion/test_c1_*.py"]},
    {"id": "WRK", "depends": ["JRN"], "source": [PY + "app/harness/worker/**"],
     "tests": [PY + "tests/unit/harness/worker/**", "integration-tests/harness/fault/test_worker.py"]},
    {"id": "FEC", "depends": [], "source": ["frontend/app/(main)/chat/**"],
     "tests": ["frontend/app/(main)/chat/**/*.test.ts", "frontend/tests/e2e/chat/**"]},
    {"id": "SKL", "depends": [], "source": [PY + "app/harness/skills/**"],
     "tests": [PY + "tests/unit/harness/skills/**", NODE + "tests/modules/agent-harness/support/skill-contract.ts",
               NODE + "tests/modules/agent-harness/skills.mongo.itest.ts"]},
]
TEST_FILES = [
    NODE + "tests/modules/agent-harness/journal-store.test.ts",
    PY + "tests/unit/harness/journal/test_client.py",
    PY + "tests/unit/harness/worker/test_claim.py",
    "integration-tests/harness/fault/test_worker.py",
    "frontend/app/(main)/chat/__tests__/stream.test.ts",
    "frontend/tests/e2e/chat/durable.spec.ts",
    PY + "tests/unit/harness/skills/test_skills.py",
    NODE + "tests/modules/agent-harness/support/skill-contract.ts",
    NODE + "tests/modules/agent-harness/skills.mongo.itest.ts",
    PY + "tests/cohesion/test_c1_single_definitions.py",
    "scripts/test_gap_totals.py",
    "scripts/test_qa_matrix.py",
]


def select(*changed):
    return at.select(list(changed), COMPONENTS, TEST_FILES)


class SelectTest(unittest.TestCase):
    def test_source_change_selects_its_component_its_dependents_and_cohesion_once(self):
        sel = select(NODE + "src/modules/agent-harness/journal/store.ts")
        self.assertFalse(sel.everything)
        self.assertEqual(sel.components, ["JRN"])
        self.assertEqual(sel.dependents, ["WRK"])
        self.assertEqual(sel.python, ["tests/cohesion", "tests/unit/harness/journal/test_client.py",
                                      "tests/unit/harness/worker/test_claim.py"])
        self.assertEqual(sel.node, ["tests/cohesion/**/*.test.ts", "tests/modules/agent-harness/journal-store.test.ts"])
        self.assertEqual(sel.frontend, [])
        self.assertEqual(sel.needs_stack, ["integration-tests/harness/fault/test_worker.py"])

    def test_dependency_is_followed_one_way_only(self):
        sel = select(PY + "app/harness/worker/claim.py")
        self.assertEqual((sel.components, sel.dependents), (["WRK"], []))
        self.assertNotIn("tests/unit/harness/journal/test_client.py", sel.python)

    def test_frontend_unit_tests_run_and_browser_specs_need_a_stack(self):
        sel = select("frontend/app/(main)/chat/stream.ts")
        self.assertEqual(sel.frontend, ["app/(main)/chat/__tests__/stream.test.ts"])
        self.assertEqual(sel.needs_stack, ["frontend/tests/e2e/chat/durable.spec.ts"])

    def test_contract_support_modules_are_not_run_on_their_own_and_itests_need_a_stack(self):
        sel = select(PY + "app/harness/skills/manager.py")
        self.assertEqual(sel.node, ["tests/cohesion/**/*.test.ts"])
        self.assertEqual(sel.needs_stack, [NODE + "tests/modules/agent-harness/skills.mongo.itest.ts"])

    def test_engines_skills_assembly_and_the_run_definition_select_the_docker_lane(self):
        for path in ("app/harness/assembly/items/skills.py", "app/harness/engines/durable.py", "harness_core/skills/builder.py",
                     "harness_core/engines/controller.py", "app/agents/run_definition.py", "app/agents/agent_loop/skills/graph_store.py",
                     "tests/unit/harness/engines/test_e2e3_tool_matrix.py", "harness_core/sandbox/broker.py"):
            self.assertEqual(select(PY + path).docker, [PY + path], path)
        for path in (PY + "harness_core/journal/types.py", "backend/nodejs/apps/src/modules/control-plane/index.ts", "docs/x.md"):
            self.assertEqual(select(path).docker, [], path)
        text = at.render_text(select(PY + "app/harness/engines/durable.py"), "a..b")
        self.assertIn("docker tests/unit tests/cohesion", text)

    def test_documentation_alone_selects_only_the_cohesion_set(self):
        sel = select("docs/test-plan/05-traceability-and-gaps.md", "docs/test-plan/data/gaps.tsv", "README.md")
        self.assertFalse(sel.everything)
        self.assertEqual((sel.python, sel.node, sel.frontend, sel.scripts),
                         (["tests/cohesion"], ["tests/cohesion/**/*.test.ts"], [], []))

    def test_ci_lint_and_api_doc_configuration_select_only_the_cohesion_set(self):
        sel = select(".github/workflows/unit-test.yml", NODE + "eslint.config.mjs", "frontend/playwright.config.ts",
                     NODE + "src/modules/api-docs/pipeshub-openapi.yaml")
        self.assertFalse(sel.everything)
        self.assertEqual((sel.python, sel.node, sel.frontend), (["tests/cohesion"], ["tests/cohesion/**/*.test.ts"], []))

    def test_a_port_contract_suite_belongs_to_its_component_not_the_whole_kit(self):
        comps = [*COMPONENTS, {"id": "JRNX", "depends": [], "source": [PY + "harness_core/testing/contracts/journal.py"],
                               "tests": [PY + "tests/unit/harness/journal/**"]}]
        sel = at.select([PY + "harness_core/testing/contracts/journal.py"], comps, TEST_FILES)
        self.assertFalse(sel.everything)
        self.assertEqual(sel.components, ["JRNX"])

    def test_a_readme_inside_a_component_is_still_the_component(self):
        sel = select(PY + "app/harness/skills/README.md")
        self.assertEqual(sel.components, ["SKL"])

    def test_suite_fixtures_select_their_whole_suite(self):
        sel = select("backend/python/tests/contracts/golden/chat_llm_error.sse",
                     NODE + "tests/contracts/support/golden.ts")
        self.assertFalse(sel.everything)
        self.assertIn("tests/contracts", sel.python)
        self.assertIn("tests/contracts/**/*.test.ts", sel.node)

    def test_top_level_script_selects_the_scripts_tests(self):
        sel = select("scripts/qa_matrix.py")
        self.assertFalse(sel.everything)
        self.assertEqual(sel.scripts, ["scripts/test_gap_totals.py", "scripts/test_qa_matrix.py"])


class VectorTest(unittest.TestCase):
    VECTOR = "contracts/harness/vectors/routing.json"

    def readers(self, *paths):
        return lambda path: list(paths) if path == self.VECTOR else []

    def test_a_vector_selects_exactly_its_readers(self):
        sel = at.select([self.VECTOR], COMPONENTS, TEST_FILES, self.readers(
            PY + "tests/unit/harness/test_routing_vectors.py", NODE + "tests/modules/agent-harness/journal-store.test.ts"))
        self.assertFalse(sel.everything, sel.reasons)
        self.assertEqual(sel.components, [])
        self.assertEqual(sel.python, ["tests/cohesion", "tests/unit/harness/test_routing_vectors.py"])
        self.assertEqual(sel.node, ["tests/cohesion/**/*.test.ts", "tests/modules/agent-harness/journal-store.test.ts"])

    def test_a_reader_that_is_component_support_selects_its_component(self):
        sel = at.select([self.VECTOR], COMPONENTS, TEST_FILES,
                        self.readers(NODE + "tests/modules/agent-harness/support/skill-contract.ts"))
        self.assertEqual(sel.components, ["SKL"])

    def test_a_reader_in_suite_support_runs_that_suite(self):
        sel = at.select([self.VECTOR], COMPONENTS, TEST_FILES, self.readers(NODE + "tests/contracts/support/durable.ts"))
        self.assertIn("tests/contracts/**/*.test.ts", sel.node)

    def test_no_reader_found_selects_everything(self):
        sel = at.select([self.VECTOR], COMPONENTS, TEST_FILES, self.readers())
        self.assertTrue(sel.everything)
        self.assertEqual(sel.reasons, [f"vector with no reader found: {self.VECTOR}"])

    def test_a_reader_that_is_shared_test_support_selects_everything(self):
        comps = [*COMPONENTS, {"id": "KIT", "depends": [], "source": [PY + "tests/support/**"], "tests": []}]
        sel = at.select([self.VECTOR], comps, TEST_FILES, self.readers(PY + "tests/support/token_vector.py"))
        self.assertTrue(sel.everything)
        self.assertEqual(sel.reasons, [f"vector read by shared test support: {self.VECTOR} ({PY}tests/support/token_vector.py)"])

    def test_codegen_output_of_a_vector_stays_fail_safe(self):
        sel = at.select([PY + "harness_core/contracts/vectors/token_patterns.py"], COMPONENTS, TEST_FILES,
                        self.readers(PY + "tests/unit/harness/journal/test_client.py"))
        self.assertTrue(sel.everything)

    def test_readers_are_found_by_name_in_the_real_tree(self):
        readers = at.vector_consumers(self.VECTOR)
        self.assertIn("backend/python/tests/unit/harness/test_qa8_routing_vectors.py", readers)
        self.assertNotIn("backend/python/tests/unit/harness/test_run_claimer.py", readers)


class FailSafeTest(unittest.TestCase):
    def assert_everything(self, path, reason):
        sel = select(NODE + "src/modules/agent-harness/journal/store.ts", path)
        self.assertTrue(sel.everything, path)
        self.assertEqual(sel.reasons, [f"{reason}: {path}"])
        self.assertEqual(sel.python, at.EVERYTHING["python"])
        self.assertEqual(sel.node, at.EVERYTHING["node"])
        self.assertTrue(sel.frontend and sel.scripts)

    def test_shared_contract_schema(self):
        self.assert_everything("contracts/harness/core/run-spec.schema.json", "shared")

    def test_vector_with_no_reader(self):
        sel = at.select(["contracts/harness/vectors/unread.json"], COMPONENTS, TEST_FILES, lambda p: [])
        self.assertTrue(sel.everything)

    def test_vector_outside_contracts(self):
        self.assert_everything("frontend/lib/contracts/harness/vectors/token-patterns.ts", "shared")
        self.assert_everything(PY + "harness_core/contracts/vectors/token_patterns.py", "shared")

    def test_codegen_output(self):
        self.assert_everything(NODE + "src/modules/agent-harness/contracts/generated/core/enums.ts", "shared")
        self.assert_everything(NODE + "src/modules/control-plane/contracts/release-catalog.generated.ts", "shared")

    def test_shared_test_support(self):
        self.assert_everything(PY + "harness_core/testing/scripted_model.py", "shared")
        self.assert_everything(PY + "tests/support/record_access.py", "shared")
        self.assert_everything(NODE + "tests/helpers/no-network-global.ts", "shared")
        self.assert_everything(NODE + ".mocharc.yaml", "shared")

    def test_file_no_component_claims(self):
        self.assert_everything(NODE + "src/modules/agent-harness/ports/journal-store.ts", "unclaimed")
        self.assert_everything("backend/python/pyproject.toml", "unclaimed")
        self.assert_everything("backend/nodejs/apps/src/modules/enterprise_search/utils/utils.ts", "unclaimed")

    def test_unclaimed_lists_only_the_paths_that_widen(self):
        paths = [PY + "app/harness/worker/a.py", "contracts/x.json", "docs/a.md", PY + "app/harness/other.py"]
        self.assertEqual(at.unclaimed(paths, COMPONENTS), [PY + "app/harness/other.py"])


class RealMapTest(unittest.TestCase):
    def test_every_tracked_harness_file_is_claimed(self):
        self.assertEqual(at.unclaimed_harness_files(), [])

    def test_composition_roots_are_shared_not_one_component(self):
        sel = at.select(["backend/nodejs/apps/src/modules/agent-harness/composition.ts"], [], [])
        self.assertEqual(sel.reasons, ["shared: backend/nodejs/apps/src/modules/agent-harness/composition.ts"])

    def test_a_worker_change_selects_the_worker_suites_of_the_real_map(self):
        comps = json.loads(at.COMPONENTS.read_text())["components"]
        sel = at.select(["backend/python/app/harness/worker/claimer.py"], comps, at.walk())
        self.assertFalse(sel.everything, sel.reasons)
        self.assertIn("WRK", sel.components)
        self.assertIn("tests/unit/harness/test_run_claimer.py", sel.python)
        self.assertIn("tests/modules/agent-harness/steering.http.test.ts", sel.node)


def _junit(path: Path, tests: int, failures: int) -> None:
    path.write_text(f'<testsuites><testsuite name="x" tests="{tests}" failures="{failures}" errors="0" skipped="1"/>'
                    "</testsuites>")


def _argfile(argv: list[str]) -> list[str]:
    return Path(next(a for a in argv if a.startswith("@"))[1:]).read_text().splitlines()


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        (self.root / PY / "tests/cohesion").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/test_x.py").write_text("")
        self.out = Path(self.tmp.name) / "lanes/abc123"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, failures=0):
        def fake(argv, cwd, env, log):
            self.calls.append((argv, cwd))
            junit = next(a.split("=", 1)[1] for a in argv if a.startswith("--junitxml="))
            _junit(Path(junit), 5, failures)
            log.write_text("ran\n")
            return 1 if failures else 0
        return fake

    def selection(self, **kw):
        return at.Selection(python=["tests/cohesion", "tests/unit/gone.py"], scripts=["scripts/test_x.py"], **kw)

    def test_writes_junit_and_summary_keyed_by_sha(self):
        summary = at.run(self.selection(), self.out, {"sha": "abc123"}, runner=self.runner(), root=self.root)
        self.assertTrue(summary["ok"])
        self.assertEqual([r["suite"] for r in summary["lanes"]], ["python", "scripts"])
        self.assertEqual(summary["lanes"][0]["tests"], 5)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["sha"], "abc123")
        self.assertIn("GREEN", (self.out / "summary.txt").read_text())
        python_argv = self.calls[0][0]
        python_args = _argfile(python_argv)
        self.assertIn("tests/cohesion", python_args)
        self.assertNotIn("tests/unit/gone.py", python_args)
        self.assertIn(at.PY_MARKERS, python_argv)

    def test_a_docker_selection_runs_the_docker_marked_tests_when_the_sandbox_image_is_named(self):
        sel = self.selection(docker=[PY + "app/harness/assembly/assembler.py"])
        with mock.patch.dict(os.environ, {at.DOCKER_IMAGE_ENV: "pxh-sandbox:test"}):
            summary = at.run(sel, self.out, {"sha": "abc123"}, runner=self.runner(), root=self.root)
        self.assertTrue(summary["ok"])
        self.assertEqual([r["suite"] for r in summary["lanes"]], ["python", "scripts", "docker"])
        argv, cwd = self.calls[-1]
        self.assertEqual(cwd, self.root / PY)
        self.assertEqual(argv[argv.index("-m", 3) + 1], at.DOCKER_MARKERS)
        self.assertEqual(argv[-2:], at.DOCKER_TARGETS)
        self.assertIn("--ignore=tests/unit/containers", argv)

    def test_a_docker_selection_without_the_sandbox_image_is_red_and_says_why(self):
        sel = self.selection(docker=[PY + "harness_core/skills/builder.py"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(at.DOCKER_IMAGE_ENV, None)
            summary = at.run(sel, self.out, {"sha": "abc123"}, runner=self.runner(), root=self.root)
        self.assertFalse(summary["ok"])
        docker = summary["lanes"][-1]
        self.assertEqual([docker["suite"], docker["exit_code"]], ["docker", 2])
        self.assertIn(at.DOCKER_IMAGE_ENV, docker["note"])
        text = (self.out / "summary.txt").read_text()
        self.assertIn("RED", text)
        self.assertIn("harness_core/skills/builder.py", text)

    def test_python_command_line_stays_short_for_a_large_selection(self):
        paths = [f"tests/unit/harness/test_case_{i:05d}_with_a_long_descriptive_name.py" for i in range(3000)]
        for p in paths[:3]:
            (self.root / PY / p).parent.mkdir(parents=True, exist_ok=True)
            (self.root / PY / p).write_text("")
        (_, _, argv, _), = at.commands(at.Selection(python=paths), self.out, self.root)
        self.assertLess(sum(len(a) + 1 for a in argv), 4096)
        self.assertEqual(_argfile(argv)[-3:], paths[:3])

    def test_existing_summary_is_reused_not_rerun(self):
        at.run(self.selection(), self.out, {"sha": "abc123"}, runner=self.runner(), root=self.root)
        self.calls.clear()
        again = at.run(self.selection(), self.out, {"sha": "abc123"}, runner=self.runner(failures=1), root=self.root)
        self.assertEqual(self.calls, [])
        self.assertTrue(again["ok"])
        self.assertTrue(again["reused"])

    def test_force_reruns(self):
        at.run(self.selection(), self.out, {"sha": "abc123"}, runner=self.runner(), root=self.root)
        again = at.run(self.selection(), self.out, {"sha": "abc123"}, runner=self.runner(failures=1), root=self.root,
                       force=True)
        self.assertFalse(again["ok"])
        self.assertEqual(again["lanes"][0]["failures"], 1)

    def test_missing_node_modules_is_a_failure_not_a_skip(self):
        sel = at.Selection(node=["tests/cohesion/**/*.test.ts"])
        with mock.patch.object(at, "NODE_MODULES_SRC", self.root / "no-such-source"):
            summary = at.run(sel, self.out, {"sha": "abc123"}, runner=self.runner(), root=self.root)
        self.assertFalse(summary["ok"])
        self.assertEqual(self.calls, [])
        self.assertIn("node_modules missing", summary["lanes"][0]["note"])

    def test_missing_node_modules_is_linked_from_the_host_source(self):
        source = self.root / "nm-source"
        (source / "apps").mkdir(parents=True)
        cwd = self.root / at.NODE_ROOT
        cwd.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(at, "NODE_MODULES_SRC", source):
            self.assertEqual(at._missing_prereq("node", cwd), "")
        self.assertTrue((cwd / "node_modules").is_symlink())
        self.assertEqual((cwd / "node_modules").resolve(), (source / "apps").resolve())

    def test_node_subset_runs_with_the_config_minus_its_spec(self):
        sel = at.Selection(node=["tests/cohesion/**/*.test.ts"])
        (_, _, argv, env), types = at.commands(sel, self.out, self.root)
        self.assertEqual(argv[argv.index("--config") + 1], str(self.out / "mocharc.cjs"))
        self.assertIn("delete rc.spec", (self.out / "mocharc.cjs").read_text())
        self.assertEqual(env["TS_NODE_PROJECT"], "tsconfig.test.json")
        # Mocha does not type-check the tests, so every Node selection also runs the test tree's ratchet.
        self.assertEqual(types[0], "nodetypes")
        self.assertEqual(types[2], ["node", "tests/typecheck-ratchet.js"])

    def test_the_node_ratchet_script_and_its_baseline_exist(self):
        apps = at.ROOT / at.NODE_ROOT
        self.assertTrue((apps / "tests" / "typecheck-ratchet.js").is_file())
        self.assertIsInstance(json.loads((apps / "tests" / "typecheck-baseline.json").read_text())["errors"], int)


class VenvPythonTest(unittest.TestCase):
    """The suite's interpreter is found from the checkout, never from one machine's home directory."""

    def test_the_env_override_wins(self) -> None:
        self.assertEqual(at.venv_python(Path("/nowhere"), {"PXH_VENV_PYTHON": "/opt/py/bin/python"}),
                         Path("/opt/py/bin/python"))

    def test_the_checkouts_own_venv_is_used_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "backend/python/venv/bin/python"
            venv.parent.mkdir(parents=True)
            venv.touch()
            self.assertEqual(at.venv_python(Path(tmp), {}), venv)

    def test_a_checkout_without_a_venv_runs_with_the_current_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(at.venv_python(Path(tmp), {}), Path(sys.executable))

    def test_no_home_directory_is_written_into_the_script(self) -> None:
        self.assertNotIn("/home/", Path(at.__file__).read_text())

    def test_no_one_machines_cache_or_branch_is_a_default(self) -> None:
        text = Path(at.__file__).read_text()
        self.assertNotIn(".cache/pxh-", text)
        self.assertEqual((at.BASE, at.LANE), (os.environ.get("AFFECTED_TESTS_BASE", "origin/main"),
                                              os.environ.get("PXH_LANE", "")))


class JunitCountsTest(unittest.TestCase):
    def test_pytest_mocha_and_vitest_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.xml"
            p.write_text('<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"/>'
                         '<testsuite tests="2" failures="0" errors="1" skipped="0"/></testsuites>')
            self.assertEqual(at.junit_counts(p), {"tests": 5, "failures": 1, "errors": 1, "skipped": 1})
            p.write_text('<testsuite name="Mocha Tests" tests="75" failures="0" errors="0" skipped="2"></testsuite>')
            self.assertEqual(at.junit_counts(p)["tests"], 75)
            p.write_text('<testsuites tests="9" failures="2" errors="0" skipped="0">'
                         '<testsuite tests="9" failures="2" errors="0" skipped="0"/></testsuites>')
            self.assertEqual(at.junit_counts(p)["tests"], 9)
            self.assertEqual(at.junit_counts(Path(d) / "absent.xml"), {})


class GitRangeTest(unittest.TestCase):
    def test_default_range_is_from_the_merge_base_with_the_base_branch(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)

            def g(*args):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                               capture_output=True)

            g("init", "-q", "-b", "main")
            (repo / "a.txt").write_text("a")
            g("add", "a.txt")
            g("commit", "-qm", "base")
            g("checkout", "-qb", "harness/wp")
            (repo / "b.txt").write_text("b")
            g("add", "b.txt")
            g("commit", "-qm", "wp")
            g("checkout", "-q", "main")
            (repo / "c.txt").write_text("c")
            g("add", "c.txt")
            g("commit", "-qm", "later on integration")
            g("checkout", "-q", "harness/wp")
            rng = at.default_range(cwd=repo, base="main")
            self.assertEqual(at.changed_paths(rng, cwd=repo), ["b.txt"])


if __name__ == "__main__":
    unittest.main()
