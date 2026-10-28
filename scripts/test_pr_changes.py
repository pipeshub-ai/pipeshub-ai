"""Which CI checks a pull request's changed files run.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import ast
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
PYTHON_LEGS = MATRIX - {"matrix_storage"}
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
        self.assertFalse(checks & (PYTHON_CHECKS | MATRIX | {"frontend"}))

    def test_a_python_only_change_runs_no_node_or_frontend(self) -> None:
        checks = runs("backend/python/app/connectors/sources/jira/connector.py")
        self.assertTrue(PYTHON_CHECKS <= checks)
        self.assertFalse(checks & {"node_unit", "node_build", "frontend", "codeql_javascript"})

    def test_any_python_change_runs_every_matrix_leg(self) -> None:
        # The suites' shared conftest loads most of app/, so none can be narrowed.
        for path in (
            "backend/python/app/services/messaging/kafka/handlers/record.py",
            "backend/python/app/services/vector_db/qdrant/qdrant.py",
            "backend/python/app/modules/transformers/blob_storage.py",
            "backend/python/tests/integration/graph_db/test_new_e2e.py",
            "backend/python/pyproject.toml",
        ):
            with self.subTest(path=path):
                self.assertEqual(runs(path) & MATRIX, MATRIX)
                if path.startswith("backend/python/app/"):
                    self.assertIn("redis_cluster", runs(path))

    def test_a_compose_file_the_graph_legs_start_runs_them(self) -> None:
        for name in ("messaging", "vector-db", "graph-db"):
            with self.subTest(name=name):
                self.assertIn("matrix_graph", runs(f"deployment/docker-compose/docker-compose.integration.{name}.yml"))

    def test_node_storage_runs_the_storage_legs_only(self) -> None:
        for path in (
            "backend/nodejs/apps/src/modules/storage/storage.service.ts",
            "backend/nodejs/apps/src/libs/services/keyValueStore.service.ts",
        ):
            with self.subTest(path=path):
                self.assertEqual(runs(path) & MATRIX, {"matrix_storage"})

    def test_node_code_outside_storage_runs_no_matrix_leg(self) -> None:
        self.assertEqual(runs("backend/nodejs/apps/src/modules/enterprise_search/controller/es_controller.ts") & MATRIX, set())

    def test_the_redis_cluster_job_runs_on_any_backend_change(self) -> None:
        # Its Node step runs the whole suite in cluster mode, and its Python suite
        # imports the messaging scenarios.
        for path in (
            "backend/nodejs/apps/tests/libs/services/redis/clusterRedisProvider.integration.test.ts",
            "backend/nodejs/apps/src/libs/errors/backend-error.ts",
            "backend/nodejs/apps/.mocharc.yaml",
            "backend/nodejs/apps/tsconfig.test.json",
            "backend/python/tests/integration/messaging/report_scenario.py",
            "backend/python/tests/support/require_service.py",
        ):
            with self.subTest(path=path):
                self.assertIn("redis_cluster", runs(path))
        self.assertNotIn("redis_cluster", runs("frontend/app/(main)/chat/page.tsx"))

    def test_a_helper_the_integration_suites_import_runs_every_python_leg(self) -> None:
        for path in (
            "backend/python/tests/support/vector_db.py",
            "backend/python/tests/unit/modules/pipeline/fakes.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(runs(path) & PYTHON_LEGS, PYTHON_LEGS)
        self.assertEqual(runs("backend/python/tests/unit/modules/pipeline/test_other.py") & MATRIX, set())

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
        # Every old trigger still starts the storage job; every one outside Node
        # still starts every Python leg, which run no Node.
        for glob in self._paths("backend-matrix.yml"):
            with self.subTest(glob=glob):
                checks = runs(_example(glob))
                self.assertIn("matrix_storage", checks)
                if not glob.startswith("backend/nodejs/"):
                    self.assertEqual(checks & PYTHON_LEGS, PYTHON_LEGS)

    def test_every_backend_matrix_suite_has_a_check(self) -> None:
        include = _workflow("backend-matrix.yml")["jobs"]["backend"]["strategy"]["matrix"]["include"]
        for suite in {entry["suite"] for entry in include} | {"storage"}:
            self.assertIn(f"matrix_{suite}", pc.NAMES)

    def test_the_codeql_languages_match_the_checks(self) -> None:
        self.assertEqual(set(pc.CODEQL_LANGUAGES), {name for name in pc.NAMES if name.startswith("codeql_")})


_TESTS_IMPORT = re.compile(r"^\s*(?:from|import)\s+(tests(?:\.\w+)+)", re.M)


def _module_file(python: Path, module: str) -> Path | None:
    """The file ``module`` names, or for ``from tests.a import b`` the one ``tests.a`` names."""
    while module.count(".") >= 1:
        path = python.joinpath(*module.split("."))
        for candidate in (path.with_suffix(".py"), path / "__init__.py"):
            if candidate.exists():
                return candidate
        module = module.rsplit(".", 1)[0]
    return None


def _helpers_the_integration_tests_import() -> set[str]:
    python = ROOT / "backend" / "python"
    integration = python / "tests" / "integration"
    todo = list(integration.rglob("*.py"))
    seen: set[Path] = set()
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for module in _TESTS_IMPORT.findall(path.read_text(encoding="utf-8", errors="ignore")):
            found = _module_file(python, module)
            if found is not None and found not in seen:
                todo.append(found)
    return {
        path.relative_to(ROOT).as_posix()
        for path in seen
        if integration not in path.parents and path.name != "__init__.py"
    }


class TestEveryImportedHelperIsCovered(unittest.TestCase):
    """A helper the integration suites import must start them when it changes."""

    def test_the_matrix_rules_and_trigger_list_every_helper(self) -> None:
        helpers = _helpers_the_integration_tests_import()
        self.assertTrue(helpers, "the import walk found nothing, so it checked nothing")
        on = _workflow("backend-matrix.yml").get(True) or _workflow("backend-matrix.yml")["on"]
        trigger = on["pull_request"]["paths"]
        for path in sorted(helpers):
            with self.subTest(path=path):
                self.assertTrue(pc.matches(path, pc.MATRIX_PYTHON))
                self.assertTrue(pc.matches(path, trigger))


class TestTheActionAndWorkflowsAgree(unittest.TestCase):
    def test_the_action_exposes_every_output(self) -> None:
        action = yaml.safe_load((ROOT / ".github/actions/pr-changes/action.yml").read_text())
        self.assertEqual(set(action["outputs"]), set(pc.NAMES) | {"codeql_languages", "integration_shards"})

    def test_workflows_read_only_outputs_that_exist(self) -> None:
        known = set(pc.NAMES) | {"codeql_languages", "integration_shards"}
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
        self.assertEqual(json.loads(written["integration_shards"]), ["core"])


ALL_SHARDS = set(pc.IT_SHARDS)
CONNECTOR_SHARD_NAMES = set(pc.CONNECTOR_SHARDS)
IT = ROOT / "integration-tests"
APP = ROOT / "backend" / "python" / "app"


def shards(*paths: str) -> set[str]:
    return set(pc.integration_shards(list(paths)))


class TestIntegrationShards(unittest.TestCase):
    def test_docs_unit_tests_and_other_workflows_run_no_shard(self) -> None:
        for path in (
            "README.md",
            "backend/python/tests/unit/connectors/test_x.py",
            "backend/nodejs/apps/tests/modules/auth/auth.test.ts",
            "frontend/lib/format.test.ts",
            "integration-tests/unit/test_indexing_wait.py",
            ".github/workflows/unit-test.yml",
            "scripts/test_pr_changes.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(shards(path), set())

    def test_the_helper_unit_tests_still_run_when_no_shard_does(self) -> None:
        self.assertIn("integration_helpers", runs("integration-tests/unit/test_indexing_wait.py"))
        self.assertNotIn("integration_helpers", runs("backend/python/tests/unit/x.py"))

    def test_a_connectors_own_code_runs_its_shard_and_core(self) -> None:
        self.assertEqual(shards("backend/python/app/connectors/sources/zendesk/connector.py"), {"connectors-2", "core"})
        self.assertEqual(shards("backend/python/app/sources/client/s3/s3.py"), {"connectors-1", "connectors-3", "core"})
        self.assertEqual(shards("backend/python/app/sources/external/google/drive.py"), {"connectors-2", "connectors-4", "core"})

    def test_a_connector_with_no_suite_runs_core(self) -> None:
        self.assertEqual(shards("backend/python/app/connectors/sources/box/connector.py"), {"core"})

    def test_shared_or_unlisted_connector_code_runs_every_shard(self) -> None:
        for path in (
            "backend/python/app/sources/client/http/http_client.py",
            "backend/python/app/sources/client/iclient.py",
            "backend/python/app/connectors/sources/atlassian/core/html_utils.py",
            "backend/python/app/connectors/sources/microsoft/common.py",
            "backend/python/app/connectors/sources/a_new_one/connector.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(shards(path), ALL_SHARDS)

    def test_a_connectors_tests_run_its_shard(self) -> None:
        self.assertEqual(shards("integration-tests/connectors/jira/test_jira_full.py"), {"connectors-2"})
        self.assertEqual(shards("integration-tests/connectors/sharepoint/test_x.py"), {"connectors-1", "connectors-3"})
        self.assertEqual(shards("integration-tests/connectors/demo/test_demo.py"), {"demo"})
        self.assertEqual(shards("integration-tests/connectors/cifs/test_x.py"), {"core"})
        self.assertEqual(shards("integration-tests/connectors/scenario_matrix.py"), ALL_SHARDS)

    def test_core_only_tests_and_the_frontend_run_core(self) -> None:
        for path in (
            "integration-tests/security/test_connection_limits.py",
            "integration-tests/resilience/test_restart.py",
            "frontend/app/(main)/chat/page.tsx",
            "frontend/tests/e2e/workspace/oauth-apps.spec.ts",
        ):
            with self.subTest(path=path):
                self.assertEqual(shards(path), {"core"})

    def test_the_demo_model_list_runs_demo(self) -> None:
        self.assertEqual(shards("backend/python/tests/evals/chat_models.py"), {"demo"})

    def test_shared_code_and_the_suites_own_setup_run_every_shard(self) -> None:
        for path in (
            "backend/nodejs/apps/src/modules/auth/routes/userAccount.routes.ts",
            "backend/python/app/modules/indexing/run.py",
            "integration-tests/helper/pipeshub_users.py",
            "integration-tests/conftest.py",
            "integration-tests/retrieval/test_x.py",
            "integration-tests/uv.lock",
            "deployment/docker-compose/docker-compose.integration.neo4j.yml",
            ".github/workflows/integration-tests.yml",
            "scripts/shard_balance.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(shards(path), ALL_SHARDS)

    def test_a_change_to_these_rules_or_no_list_runs_every_shard(self) -> None:
        self.assertEqual(shards("README.md", "scripts/pr_changes.py"), ALL_SHARDS)
        self.assertEqual(shards(), ALL_SHARDS)
        self.assertEqual(set(pc.integration_shards(["README.md"] * pc.FILES_API_LIMIT)), ALL_SHARDS)

    def test_shards_come_out_in_the_workflows_order(self) -> None:
        listed = list(pc.integration_shards(["frontend/app/page.tsx", "integration-tests/connectors/jira/test_x.py"]))
        self.assertEqual(listed, ["connectors-2", "core"])


def _imports(path: Path, package: str) -> set[str]:
    """Absolute module names a file imports, with relative imports resolved."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            found.add(module)
            found |= {f"{module}.{alias.name}" for alias in node.names}
    return found


def _connector_folder(module_path: str) -> str | None:
    """The connector folder name a slash path under one of the roots is in."""
    for root in pc.CONNECTOR_ROOTS:
        prefix = root.removeprefix("backend/python/") + "/"
        if module_path.startswith(prefix):
            rest = module_path[len(prefix):].split("/")
            return rest[0] if len(rest) > 1 else None
    return None


class TestTheShardRulesMatchTheRepository(unittest.TestCase):
    def test_the_connector_shards_match_the_workflow(self) -> None:
        text = (WORKFLOWS / "integration-tests.yml").read_text()
        listed = {
            f"connectors-{n}": tuple(markers.split(" or "))
            for n, markers in re.findall(r'CONN_SHARD_(\d): "([^"]+)"', text)
        }
        self.assertEqual(listed, pc.CONNECTOR_SHARDS)

    def test_it_all_runs_the_same_shards_the_rules_know(self) -> None:
        text = (WORKFLOWS / "integration-tests.yml").read_text()
        it_all = re.search(r"'it-all'\) && '(\[[^']+\])'", text)
        self.assertIsNotNone(it_all)
        self.assertEqual(json.loads(it_all.group(1)), list(pc.IT_SHARDS))

    def test_every_connector_test_file_carries_a_marker_of_its_shards(self) -> None:
        marker = re.compile(r"pytest\.mark\.(\w+)")
        checked = 0
        for folder in sorted(p for p in (IT / "connectors").iterdir() if p.is_dir() and not p.name.startswith("__")):
            expected = pc._integration_shards_for(f"integration-tests/connectors/{folder.name}/x.py")
            for path in folder.rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                if not re.search(r"^\s*(?:async )?def test_", text, re.M):
                    continue
                checked += 1
                found = {pc._SHARD_OF[m] for m in marker.findall(text) if m in pc._SHARD_OF}
                with self.subTest(path=str(path.relative_to(ROOT))):
                    if expected == {"core"}:
                        self.assertEqual(found, set())
                    else:
                        self.assertTrue(found, "no connector marker, so core runs it")
                        self.assertLessEqual(found, expected)
        self.assertGreater(checked, 50)
        for name in pc.CONNECTOR_TEST_MARKERS:
            self.assertTrue((IT / "connectors" / name).is_dir(), name)

    def test_core_only_folders_hold_no_shard_markers_and_neither_do_their_importers(self) -> None:
        # A change to a core-only folder runs core alone, so every test it can
        # reach, its own and those of any folder that imports it, must run there.
        core_folders = {glob.split("/")[1] for glob in pc.IT_CORE if glob.startswith("integration-tests/")}
        folders = {p.name.replace("-", "_"): p.name for p in IT.iterdir() if p.is_dir()}
        imported_by: dict[str, set[str]] = {}
        for path in IT.rglob("*.py"):
            if ".venv" in path.parts or len(path.relative_to(IT).parts) < 2:
                continue
            here = path.relative_to(IT).parts[0]
            for module in _imports(path, ""):
                top = folders.get(module.split(".")[0])
                if top and top != here:
                    imported_by.setdefault(top, set()).add(here)
        reach = set(core_folders)
        todo = list(core_folders)
        while todo:
            for importer in imported_by.get(todo.pop(), ()):
                if importer not in reach:
                    reach.add(importer)
                    todo.append(importer)
        self.assertIn("pattern_match", reach, "the import walk found nothing, so it checked nothing")
        marker = re.compile(r"pytest\.mark\.(\w+)")
        for folder in sorted(reach):
            for path in (IT / folder).rglob("*.py"):
                found = {m for m in marker.findall(path.read_text(encoding="utf-8", errors="ignore")) if m in pc._SHARD_OF}
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertEqual(found, set())

    def test_a_listed_connector_folder_is_imported_only_where_its_shards_cover(self) -> None:
        allowed = ("app/agents/actions/", "app/agents/tools/", "app/connectors/core/")
        python = ROOT / "backend" / "python"
        checked = 0
        for path in APP.rglob("*.py"):
            rel = path.relative_to(python).as_posix()
            package = ".".join(path.relative_to(python).parent.parts)
            mine = _connector_folder(rel)
            for module in _imports(path, package):
                name = _connector_folder(module.replace(".", "/") + "/x")
                if name is None or name == mine or name not in pc.CONNECTOR_CODE_MARKERS:
                    continue
                checked += 1
                covers = {"core"} | pc._shards_of(pc.CONNECTOR_CODE_MARKERS[name])
                with self.subTest(importer=rel, imports=name):
                    if mine is None:
                        self.assertTrue(rel.startswith(allowed), "imported by product code: leave it unlisted")
                    else:
                        importer = pc.CONNECTOR_CODE_MARKERS.get(mine)
                        self.assertIsNotNone(importer, f"{mine} runs every shard, so {name} must too")
                        self.assertLessEqual({"core"} | pc._shards_of(importer), covers)
        self.assertGreater(checked, 100)

    def test_a_suite_that_imports_connector_or_backend_test_code_runs_when_it_changes(self) -> None:
        checked = 0
        for path in IT.rglob("*.py"):
            if ".venv" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            needed = pc._integration_shards_for(rel)
            for module in _imports(path, ""):
                if module.startswith("app."):
                    target = "backend/python/" + module.replace(".", "/") + "/x"
                elif module.startswith("tests."):
                    target = "backend/python/" + module.replace(".", "/") + ".py"
                else:
                    continue
                if _connector_folder(target.removeprefix("backend/python/")) is None and not module.startswith("tests."):
                    continue
                checked += 1
                with self.subTest(importer=rel, imports=module):
                    self.assertLessEqual(needed, pc._integration_shards_for(target))
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
