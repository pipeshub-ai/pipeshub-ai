"""Tests for scripts/harness_test_inventory.py (run: python3 -m unittest discover -s scripts -p 'test_*.py')."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness_test_inventory as inv  # noqa: E402


class GlobTest(unittest.TestCase):
    def test_double_star_matches_any_depth_including_none(self):
        rx = inv.glob_to_regex("backend/python/tests/unit/harness/**")
        self.assertTrue(rx.match("backend/python/tests/unit/harness/test_a.py"))
        self.assertTrue(rx.match("backend/python/tests/unit/harness/x/y/test_a.py"))
        self.assertFalse(rx.match("backend/python/tests/unit/harness_core/test_a.py"))

    def test_star_stays_inside_one_segment(self):
        rx = inv.glob_to_regex("tests/modules/agent-harness/session-*.ts")
        self.assertTrue(rx.match("tests/modules/agent-harness/session-forks.test.ts"))
        self.assertFalse(rx.match("tests/modules/agent-harness/sub/session-forks.test.ts"))

    def test_parentheses_in_paths_are_literal(self):
        rx = inv.glob_to_regex("frontend/app/(main)/chat/**/*.test.ts")
        self.assertTrue(rx.match("frontend/app/(main)/chat/__tests__/api.test.ts"))


class ClassifyTest(unittest.TestCase):
    def test_python_levels_follow_the_tree_and_markers(self):
        c = inv.classify
        self.assertEqual(c("backend/python/tests/unit/harness/test_x.py", []), "unit")
        self.assertEqual(c("backend/python/tests/unit/harness/test_x.py", ["docker"]), "integration-docker")
        self.assertEqual(c("backend/python/tests/unit/harness/test_x.py", ["integration"]), "integration")
        self.assertEqual(c("backend/python/tests/unit/harness/test_x.py", ["slow"]), "gate-slow")
        self.assertEqual(c("backend/python/tests/unit/harness/test_x_contract.py", []), "contract")
        self.assertEqual(c("backend/python/tests/cohesion/test_c2.py", []), "cohesion")
        self.assertEqual(c("backend/python/tests/evals/test_live.py", ["slow"]), "answer-quality-live")

    def test_node_and_frontend_levels(self):
        c = inv.classify
        self.assertEqual(c("backend/nodejs/apps/tests/modules/agent-harness/a.mongo.itest.ts", []), "integration")
        self.assertEqual(c("backend/nodejs/apps/tests/modules/agent-harness/a.http.test.ts", []), "component")
        self.assertEqual(c("backend/nodejs/apps/tests/modules/agent-harness/support/x-contract.ts", []), "contract")
        self.assertEqual(c("frontend/tests/e2e/chat/a.spec.ts", []), "e2e-browser")
        self.assertEqual(c("integration-tests/harness/fault/test_crash_points.py", []), "fault")


class FactsTest(unittest.TestCase):
    def test_markers_and_test_names(self):
        text = (
            "import pytest\n"
            "pytestmark = [pytest.mark.docker]\n"
            "@pytest.mark.slow\n"
            "def test_a():\n    pass\n"
            "class TestB:\n"
            "    @pytest.mark.parametrize('x', [1])\n"
            "    def test_c(self, x):\n        pass\n"
        )
        markers, names = inv.python_facts(text)
        self.assertEqual(markers, ["docker", "slow"])
        self.assertEqual(names, ["test_a", "TestB.test_c"])

    def test_contract_subclass_counts_inherited_tests(self):
        inv.CLASS_INDEX.clear()
        inv.CLASS_INDEX.update({"PortContract": ([], ["test_one", "test_two"])})
        try:
            _, names = inv.python_facts("class TestInMemory(PortContract):\n    pass\n")
        finally:
            inv.CLASS_INDEX.clear()
        self.assertEqual(names, ["TestInMemory.test_one", "TestInMemory.test_two"])

    def test_typescript_it_and_test_calls(self):
        names = inv.ts_facts("describe('x', () => {\n  it('does a', () => {});\n  it.skip(\"does b\", () => {});\n  test(`c`, () => {});\n});\n")
        self.assertEqual(names, ["does a", "does b", "c"])


class ComponentMapTest(unittest.TestCase):
    def test_ids_are_unique_and_dependencies_resolve(self):
        data = json.loads(inv.COMPONENTS.read_text())
        ids = [c["id"] for c in data["components"]]
        self.assertEqual(len(ids), len(set(ids)))
        for c in data["components"]:
            for dep in c["depends"]:
                self.assertIn(dep, ids, f"{c['id']} depends on unknown {dep}")
            self.assertIn(c["owner"], data["owners"])


if __name__ == "__main__":
    unittest.main()
