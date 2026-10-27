"""Which CI checks a pull request's changed files run.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_changes as pc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
MATRIX = {name for name in pc.NAMES if name.startswith("matrix_")}
PYTHON_CHECKS = {"python_unit", "codeql_python"}


def runs(*paths: str) -> set[str]:
    return set(pc.decide(list(paths)))


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _example(glob: str) -> str:
    """A path the glob matches: each wildcard replaced by a plausible name."""
    path = glob.replace("**/", "deep/er/").replace("**", "deep/file.py").replace("*", "x")
    return path.replace("?", "x")


class TestGlobs(unittest.TestCase):
    def test_a_single_star_stays_inside_one_directory(self) -> None:
        self.assertTrue(pc.matches("backend/python/tests/integration/conftest.py", ["backend/python/tests/integration/*.py"]))
        self.assertFalse(pc.matches("backend/python/tests/integration/kv/test_a.py", ["backend/python/tests/integration/*.py"]))

    def test_a_double_star_spans_directories_or_none(self) -> None:
        self.assertTrue(pc.matches("README.md", ["**/*.md"]))
        self.assertTrue(pc.matches("frontend/app/a/b.md", ["**/*.md"]))
        self.assertTrue(pc.matches("frontend/app/a/b.tsx", ["frontend/**"]))
        self.assertFalse(pc.matches("frontendx/a.tsx", ["frontend/**"]))


class TestDecisions(unittest.TestCase):
    def test_a_docs_only_change_runs_nothing(self) -> None:
        self.assertEqual(runs("changelog/0.10.0.md", "docs/indexing-service.md", ".github/dependabot.yml"), set())

    def test_a_node_only_change_runs_no_python(self) -> None:
        checks = runs("backend/nodejs/apps/src/libs/errors/backend-error.ts")
        self.assertTrue({"node_unit", "node_build", "codeql_javascript", "e2e_smoke"} <= checks)
        self.assertFalse(checks & (PYTHON_CHECKS | MATRIX | {"redis_cluster", "frontend"}))

    def test_a_python_only_change_runs_no_node_or_frontend(self) -> None:
        checks = runs("backend/python/app/connectors/sources/jira/connector.py")
        self.assertTrue(PYTHON_CHECKS <= checks)
        self.assertFalse(checks & {"node_unit", "node_build", "frontend", "codeql_javascript"})

    def test_a_kafka_fix_runs_the_messaging_legs_only(self) -> None:
        checks = runs("backend/python/app/services/messaging/kafka/consumer/consumer.py")
        self.assertEqual(checks & MATRIX, {"matrix_messaging"})
        self.assertIn("redis_cluster", checks)

    def test_shared_backend_config_runs_every_matrix_leg(self) -> None:
        self.assertEqual(runs("backend/python/pyproject.toml") & MATRIX, MATRIX)

    def test_node_storage_runs_the_storage_legs_only(self) -> None:
        checks = runs("backend/nodejs/apps/src/modules/storage/storage.service.ts")
        self.assertEqual(checks & MATRIX, {"matrix_storage"})

    def test_a_frontend_change_runs_the_frontend_checks_only(self) -> None:
        checks = runs("frontend/app/(main)/chat/page.tsx")
        self.assertEqual(checks, {"frontend", "codeql_javascript", "e2e_smoke"})

    def test_a_workflow_change_runs_its_own_checks_and_the_actions_scan(self) -> None:
        checks = runs(".github/workflows/unit-test.yml")
        self.assertTrue({"node_unit", "python_unit", "redis_cluster", "codeql_actions"} <= checks)

    def test_a_change_to_these_rules_runs_everything(self) -> None:
        self.assertEqual(runs("README.md", "scripts/pr_changes.py"), set(pc.NAMES))
        self.assertEqual(runs(".github/actions/pr-changes/action.yml"), set(pc.NAMES))

    def test_no_list_or_a_cut_off_list_runs_everything(self) -> None:
        self.assertEqual(runs(), set(pc.NAMES))
        self.assertEqual(set(pc.decide(["README.md"] * pc.FILES_API_LIMIT)), set(pc.NAMES))

    def test_codeql_analyses_only_the_changed_languages(self) -> None:
        include = json.loads(pc.codeql_matrix(pc.decide(["backend/python/app/x.py"])))
        self.assertEqual(include, [{"language": "python", "build-mode": "none"}])
        self.assertEqual(json.loads(pc.codeql_matrix(pc.decide(["README.md"]))), [])
        every = json.loads(pc.codeql_matrix(pc.everything()))
        self.assertEqual([entry["language"] for entry in every], ["actions", "javascript-typescript", "python"])


class TestNothingTheOldTriggersCoveredIsDropped(unittest.TestCase):
    """Each workflow's own trigger paths still start at least one of its jobs."""

    def _paths(self, workflow: str) -> list[str]:
        on = _workflow(workflow).get(True) or _workflow(workflow)["on"]
        return on["pull_request"]["paths"]

    def test_unit_test_triggers(self) -> None:
        for glob in self._paths("unit-test.yml"):
            with self.subTest(glob=glob):
                self.assertTrue(runs(_example(glob)) & {"node_unit", "python_unit", "redis_cluster"})

    def test_backend_matrix_triggers(self) -> None:
        for glob in self._paths("backend-matrix.yml"):
            with self.subTest(glob=glob):
                self.assertTrue(runs(_example(glob)) & MATRIX)

    def test_every_backend_matrix_suite_has_a_check(self) -> None:
        include = _workflow("backend-matrix.yml")["jobs"]["backend"]["strategy"]["matrix"]["include"]
        for suite in {entry["suite"] for entry in include} | {"storage"}:
            self.assertIn(f"matrix_{suite}", pc.NAMES)

    def test_the_codeql_languages_match_the_checks(self) -> None:
        self.assertEqual(set(pc.CODEQL_LANGUAGES), {name for name in pc.NAMES if name.startswith("codeql_")})


class TestTheActionAndWorkflowsAgree(unittest.TestCase):
    def test_the_action_exposes_every_output(self) -> None:
        action = yaml.safe_load((ROOT / ".github/actions/pr-changes/action.yml").read_text())
        self.assertEqual(set(action["outputs"]), set(pc.NAMES) | {"codeql_languages"})

    def test_workflows_read_only_outputs_that_exist(self) -> None:
        known = set(pc.NAMES) | {"codeql_languages"}
        for path in WORKFLOWS.glob("*.yml"):
            used = set(re.findall(r"needs\.changes\.outputs\.(\w+)", path.read_text()))
            job = (yaml.safe_load(path.read_text()).get("jobs") or {}).get("changes") or {}
            used |= {re.sub(r"\W", "", v.split(".")[-1]) for v in (job.get("outputs") or {}).values()}
            with self.subTest(workflow=path.name):
                self.assertLessEqual(used, known)

    def test_the_outputs_are_written_for_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            listed = Path(tmp, "files.txt")
            listed.write_text("frontend/app/page.tsx\n")
            output = Path(tmp, "out")
            summary = Path(tmp, "summary")
            env = {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
            with unittest.mock.patch.dict("os.environ", env), unittest.mock.patch("sys.stdout"):
                self.assertEqual(pc.main(["--files", str(listed)]), 0)
            written = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(written["frontend"], "true")
        self.assertEqual(written["python_unit"], "false")
        self.assertEqual(json.loads(written["codeql_languages"]), [{"language": "javascript-typescript", "build-mode": "none"}])


if __name__ == "__main__":
    unittest.main()
