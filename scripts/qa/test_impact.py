"""Impact: how a change becomes the floor, for each source of selection.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import unittest

from qa import catalog, collect, config, impact, paths, testing


class ImpactCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.base = testing.git(self.root, "rev-parse", "HEAD")

    def change(self, files: dict[str, str], profile: str = "standard", scope: str = "all", branch: str | None = None) -> dict:
        for rel, body in files.items():
            testing.write(self.root, rel, body)
        if branch:
            testing.git(self.root, "checkout", "-q", "-b", branch)
        testing.commit(self.root, "change")
        cfg = config.load(self.root)
        changeset, _ = collect.collect(cfg, self.base, None, mode="pr")
        return impact.compute(cfg, changeset, catalog.build(cfg), profile=profile, scope=scope)

    def why(self, plan_input: dict, test_id: str) -> list[str]:
        return plan_input["floor"]["items"][test_id]["why"]


class TestSelectionSources(ImpactCase):
    def test_import_graph_reaches_tests_two_hops_away(self) -> None:
        p = self.change({"backend/python/app/services/graph_db/provider.py": "def get_records():\n    return [1]\n"})
        self.assertIn("py:tests/unit/services/test_retrieval.py", p["floor"]["tests"])
        self.assertIn("imports backend/python/app/services/graph_db/provider.py (2 hops)", self.why(p, "py:tests/unit/services/test_retrieval.py"))

    def test_fast_profile_follows_one_hop_only(self) -> None:
        p = self.change({"backend/python/app/services/graph_db/provider.py": "X = 1\n"}, profile="fast")
        self.assertIn("py:tests/unit/services/graph_db/test_provider.py", p["floor"]["tests"])
        self.assertNotIn("py:tests/unit/services/test_retrieval.py", p["floor"]["tests"])

    def test_mock_patch_strings_count_as_imports(self) -> None:
        p = self.change({"backend/python/app/connectors/sources/jira/sync.py": "def fetch():\n    return 2\n"})
        self.assertIn("py:tests/unit/test_patch_only.py", p["floor"]["tests"])

    def test_rule_markers_select_integration_tests(self) -> None:
        p = self.change({"backend/python/app/connectors/sources/jira/sync.py": "def fetch():\n    return 2\n"})
        self.assertIn("it:connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync", p["floor"]["tests"])
        self.assertIn("connector-jira", p["matched_rules"])

    def test_storage_abstraction_runs_every_backend(self) -> None:
        p = self.change({"backend/python/app/services/graph_db/provider.py": "X = 2\n"})
        self.assertEqual(p["stack"]["graph_db"], "both")
        self.assertEqual(p["matrix"], ["arangodb", "neo4j"])

    def test_covers_tag_selects_the_integration_test(self) -> None:
        p = self.change({"backend/python/app/services/retrieval.py": "def search():\n    return []\n"})
        self.assertIn("covers backend/python/app/services/retrieval.py", self.why(p, "it:retrieval/test_search.py::test_acl_filter"))
        # test_ranking is in the floor through flow `search`, not through a covers tag.
        self.assertFalse([r for r in self.why(p, "it:retrieval/test_search.py::test_ranking") if r.startswith("covers")])

    def test_typescript_mirror_and_import_graph(self) -> None:
        p = self.change({"backend/nodejs/apps/src/modules/auth/auth.service.ts": "export const check = () => false;\n"})
        self.assertIn("imports backend/nodejs/apps/src/modules/auth/auth.service.ts (2 hops)",
                      self.why(p, "node:tests/modules/auth/auth.controller.test.ts"))

    def test_changed_test_always_runs(self) -> None:
        p = self.change({"integration-tests/retrieval/test_search.py": "def test_ranking():\n    '''TC-RET-002: x.'''\n"})
        self.assertIn("changed test", self.why(p, "it:retrieval/test_search.py::test_ranking"))

    def test_frontend_change_adds_smoke_e2e_with_grep(self) -> None:
        p = self.change({"frontend/app/login/page.tsx": "export default function Page() { return 1; }\n"})
        self.assertEqual(p["floor"]["items"]["e2e:tests/e2e/login.spec.ts"]["grep"], "@smoke")


class TestFullRunsAndProfiles(ImpactCase):
    def test_tracked_file_runs_its_suite_in_full(self) -> None:
        p = self.change({"backend/python/pyproject.toml": "[project]\nname='x'\n"})
        self.assertIn("py-unit", p["full_suites"])
        self.assertIn("py:tests/unit/test_patch_only.py", p["floor"]["tests"])

    def test_switch_file_reruns_both_unit_suites(self) -> None:
        p = self.change({"backend/python/app/edition.py": "FLAG = 2\n"})
        self.assertIn("py-unit", p["full_suites"])
        self.assertIn("py-unit-plus", p["full_suites"])
        self.assertNotIn("node-unit", p["full_suites"])

    def test_oss_sync_branch_forces_full_profile(self) -> None:
        p = self.change({"backend/python/app/services/untested.py": "X = 1\n"}, branch="oss-sync-1-1-26")
        self.assertEqual(p["profile"], "full")

    def test_fast_profile_defers_integration_tests(self) -> None:
        p = self.change({"backend/python/app/connectors/sources/jira/sync.py": "X = 1\n"}, profile="fast")
        self.assertFalse([t for t in p["floor"]["tests"] if t.startswith("it:")])
        self.assertTrue(p["deferred"])


class TestFlows(ImpactCase):
    def test_flow_trigger_adds_flow_tests(self) -> None:
        p = self.change({"backend/python/app/services/retrieval.py": "def search():\n    return []\n"})
        rows = [t for t in p["floor"]["tests"] if t.startswith("it:retrieval/test_search.py")]
        self.assertEqual(len(rows), 2)
        for t in rows:
            self.assertIn("flow search", self.why(p, t))
        self.assertEqual(p["flows"]["search"], "trigger backend/python/app/services/retrieval.py")

    def test_critical_flow_runs_on_a_docs_change(self) -> None:
        p = self.change({"README.md": "hi\n"})
        self.assertEqual(p["flows"], {"search": "critical"})
        for t in p["floor"]["tests"]:
            self.assertEqual(self.why(p, t), ["critical flow search"])
        self.assertTrue(p["floor"]["tests"])

    def test_deleted_file_triggers_its_flow(self) -> None:
        (self.root / "backend/nodejs/apps/src/modules/auth/auth.service.ts").unlink()
        p = self.change({})
        self.assertIn("login", p["flows"])
        self.assertTrue(any(t.startswith("e2e:") and "login" in t for t in p["floor"]["tests"]), p["floor"]["tests"])

    def test_fast_profile_defers_critical_stack_tests(self) -> None:
        p = self.change({"README.md": "hi\n"}, profile="fast")
        self.assertFalse([t for t in p["floor"]["tests"] if t.startswith("it:")])
        self.assertTrue(p["deferred"])


class TestPlannerSkipped(ImpactCase):
    def test_docs_only_change_skips_the_model(self) -> None:
        p = self.change({"docs/x.md": "# x\n"})
        self.assertEqual(p["planner"], "none")
        self.assertEqual(p["planner_reason"], "no testable change")

    def test_policy_overlay_change_forces_a_full_run(self) -> None:
        p = self.change({"scripts/plus/qa/qa_hooks.py": "def edition(path):\n    return None\n"}, profile="fast")
        self.assertIn("py:tests/unit/services/test_retrieval.py", p["floor"]["tests"])
        self.assertIn("tracked file scripts/plus/qa/qa_hooks.py", self.why(p, "py:tests/unit/services/test_retrieval.py")[0])

    def test_shipped_rules_force_a_full_run_for_any_overlay(self) -> None:
        shipped = config.load(paths.REPO).always["full_run"]
        self.assertTrue(any("scripts/*/qa/**" in fr["paths"] and fr["suites"] == "*" for fr in shipped))

    def test_full_run_skips_the_model(self) -> None:
        always = testing.ALWAYS + '\n[[full_run]]\npaths = ["scripts/qa/rules/always.toml"]\nsuites = "*"\n'
        p = self.change({"scripts/qa/rules/always.toml": always})
        self.assertEqual(p["planner"], "none")
        self.assertEqual(p["planner_reason"], "full run")

    def test_runtime_text_file_keeps_the_model(self) -> None:
        p = self.change({"backend/python/app/x/SKILL.md": "# skill\n"})
        self.assertNotEqual(p["planner"], "none")

    def test_source_change_keeps_the_model(self) -> None:
        p = self.change({"backend/python/app/services/untested.py": "X = 1\n"})
        self.assertEqual(p["planner"], "default")


class TestEditionScope(ImpactCase):
    def test_base_scope_never_selects_overlay_tests(self) -> None:
        p = self.change({"backend/python/app/plus/usage.py": "LIMIT = 2\n", "backend/python/app/edition.py": "FLAG = 3\n"}, scope="oss")
        self.assertTrue(p["floor"]["tests"])
        self.assertFalse([t for t in p["floor"]["tests"] if "plus/" in t])

    def test_overlay_change_selects_the_overlay_mirror(self) -> None:
        p = self.change({"backend/python/app/plus/usage.py": "LIMIT = 2\n"}, scope="plus")
        self.assertIn("py:plus/tests/unit/test_usage.py", p["floor"]["tests"])


class TestMarkerRules(ImpactCase):
    def test_marker_rules_select_overlay_suite(self) -> None:
        testing.write(self.root, "integration-tests/plus/test_jira_plus.py", 'import pytest\n\npytestmark = pytest.mark.jira\n\n\ndef test_j():\n    """TC-PLUS-JIRA-001: jira plus."""\n')
        testing.commit(self.root, "plus jira test")
        self.base = testing.git(self.root, "rev-parse", "HEAD")
        p = self.change({"backend/python/app/connectors/sources/jira/sync.py": "def fetch():\n    return 2\n"})
        row = "it:plus/test_jira_plus.py::test_j"
        self.assertIn(row, p["floor"]["tests"])
        self.assertIn("rule connector-jira", self.why(p, row))


class TestCandidates(ImpactCase):
    def test_the_cut_keeps_tests_related_to_the_change(self) -> None:
        from unittest import mock
        testing.write(self.root, "integration-tests/invoicing/test_invoices.py", 'def test_total():\n    """TC-INV-001: invoice totals."""\n')
        testing.write(self.root, "integration-tests/aaa/test_unrelated.py", 'def test_x():\n    """TC-AAA-001: unrelated."""\n')
        testing.commit(self.root, "tests exist before the change")
        self.base = testing.git(self.root, "rev-parse", "HEAD")
        with mock.patch.object(impact, "MAX_CANDIDATES", 1):
            p = self.change({"backend/python/app/services/invoices.py": "def total():\n    return 1\n"})
        self.assertEqual([c["id"] for c in p["candidates"]], ["it:invoicing/test_invoices.py::test_total"])

    def test_zero_score_rows_are_not_candidates(self) -> None:
        testing.write(self.root, "integration-tests/aaa/test_unrelated.py", 'def test_x():\n    """TC-AAA-001: unrelated."""\n')
        testing.commit(self.root, "tests exist before the change")
        self.base = testing.git(self.root, "rev-parse", "HEAD")
        p = self.change({"backend/python/app/services/invoices.py": "def total():\n    return 1\n"})
        self.assertNotIn("it:aaa/test_unrelated.py::test_x", [c["id"] for c in p["candidates"]])


class TestGaps(ImpactCase):
    def test_source_without_a_mirror_test_is_a_missing_test(self) -> None:
        p = self.change({"backend/python/app/services/untested.py": "def helper():\n    return 2\n"})
        self.assertEqual([m["suggested_path"] for m in p["missing"]], ["backend/python/tests/unit/services/test_untested.py"])
        self.assertIn("backend/python/app/services/untested.py", p["unmapped"])

    def test_untested_file_pulls_in_only_neighbours_named_like_it(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/services/test_billing_rules.py", "def test_b():\n    pass\n")
        p = self.change({"backend/python/app/services/billing_audit.py": "def count():\n    return 1\n"})
        self.assertIn("py:tests/unit/services/test_billing_rules.py", p["floor"]["tests"])
        self.assertNotIn("py:tests/unit/services/test_retrieval.py", p["floor"]["tests"])

    def test_test_with_the_mirror_name_elsewhere_is_not_a_gap(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/test_untested.py", "def test_h():\n    pass\n")
        p = self.change({"backend/python/app/services/untested.py": "def helper():\n    return 3\n"})
        self.assertIn("same name as backend/python/app/services/untested.py", self.why(p, "py:tests/unit/test_untested.py"))
        self.assertEqual(p["missing"], [])

    def test_overlay_file_tested_in_the_base_suite_layout_is_not_a_gap(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/plus/test_pricing.py", "def test_b():\n    pass\n")
        p = self.change({"backend/python/app/plus/pricing.py": "PLAN = 1\n"}, scope="plus")
        self.assertIn("mirror of backend/python/app/plus/pricing.py", self.why(p, "py:tests/unit/plus/test_pricing.py"))
        self.assertEqual(p["missing"], [])

    def test_base_twin_test_does_not_cover_the_overlay_file(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/test_resolvers.py", "def test_r():\n    pass\n")
        p = self.change({"backend/python/app/plus/resolvers.py": "X = 1\n"}, scope="plus")
        self.assertEqual([m["suggested_path"] for m in p["missing"]], ["backend/python/plus/tests/unit/test_resolvers.py"])

    def test_new_connector_without_integration_suite_is_a_gap(self) -> None:
        p = self.change({"backend/python/app/connectors/sources/acme/sync/run.py": "X = 1\n"})
        self.assertIn("integration-tests/connectors/acme/", [m["suggested_path"] for m in p["missing"]])


if __name__ == "__main__":
    unittest.main()
