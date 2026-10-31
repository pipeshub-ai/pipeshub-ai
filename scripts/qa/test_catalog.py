"""Catalog: specs are read from tests, suites claim files by edition, the
committed catalog never lists overlay tests in the base file.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import os
import unittest
from unittest import mock

from qa import catalog, classify, config, globs, paths, testing


class TestGlobs(unittest.TestCase):
    def test_star_does_not_cross_directories(self) -> None:
        self.assertTrue(globs.match("a/b.py", "a/*.py"))
        self.assertFalse(globs.match("a/b/c.py", "a/*.py"))

    def test_double_star_matches_zero_or_more_directories(self) -> None:
        self.assertTrue(globs.match("a/b.py", "a/**/b.py"))
        self.assertTrue(globs.match("a/x/y/b.py", "a/**/b.py"))

    def test_trailing_slash_matches_everything_below(self) -> None:
        self.assertTrue(globs.match("integration-tests/connectors/jira/x.py", "integration-tests/connectors/"))


class TestClassify(unittest.TestCase):
    def test_kinds(self) -> None:
        self.assertEqual(classify.kind("backend/python/tests/unit/test_x.py"), "test")
        self.assertEqual(classify.kind("frontend/tests/e2e/a.spec.ts"), "test")
        self.assertEqual(classify.kind("backend/python/pyproject.toml"), "config")
        self.assertEqual(classify.kind(".github/workflows/qa.yml"), "infra")
        self.assertEqual(classify.kind("docs/x.md"), "docs")
        self.assertEqual(classify.kind("backend/python/app/x.py"), "src")


class TestDocstringSpec(unittest.TestCase):
    def test_tc_title_covers_needs(self) -> None:
        spec = catalog.parse_docstring("TC-RET-001: search hides records.\n\ncovers: app/a.py, app/b/\nneeds: env:TOKEN, service:graph-db")
        self.assertEqual(spec.tc, "TC-RET-001")
        self.assertEqual(spec.title, "search hides records.")
        self.assertEqual(spec.covers, ["app/a.py", "app/b/"])
        self.assertEqual(spec.needs, ["env:TOKEN", "service:graph-db"])

    def test_prose_covers_line_is_not_a_tag(self) -> None:
        spec = catalog.parse_docstring("Checks the parser.\n\ncovers: the retry path and timeouts")
        self.assertEqual(spec.covers, [])
        self.assertIsNone(spec.tc)
        self.assertEqual(spec.title, "Checks the parser.")


class TestBuild(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.cfg = config.load(self.root)
        self.rows = catalog.index(catalog.build(self.cfg))

    def test_integration_tests_are_listed_one_per_test_with_spec(self) -> None:
        row = self.rows["it:connectors/jira/test_jira.py::TestJira::test_tc_sync_001_full_sync"]
        self.assertEqual(row["tc"], "TC-SYNC-001")
        self.assertEqual(row["title"], "a full sync puts every issue in the graph.")
        self.assertEqual(row["markers"], ["integration", "jira"])
        self.assertTrue(row["ordered"])

    def test_rows_get_module_flows_priority(self) -> None:
        r = self.rows["it:retrieval/test_search.py::test_acl_filter"]
        self.assertEqual((r["module"], r["flows"], r["priority"]), ("retrieval", ["search"], "P0"))
        r = self.rows["e2e:tests/e2e/login.spec.ts"]
        self.assertEqual((r["flows"], r["priority"]), (["login"], "P1"))
        r = self.rows["py:tests/unit/test_patch_only.py"]
        self.assertEqual((r["module"], r["flows"], r["priority"]), (None, [], None))

    def test_first_module_wins(self) -> None:
        self.cfg.modules[-1]["paths"] = self.cfg.modules[-1]["paths"] + ["integration-tests/**"]
        rows = catalog.index(catalog.build(self.cfg))
        self.assertEqual(rows["it:retrieval/test_search.py::test_acl_filter"]["module"], "retrieval")

    def test_overlay_flow_absent_without_overlay(self) -> None:
        root, tmp = testing.make_repo(overlay=False)
        self.addCleanup(tmp.cleanup)
        rows = catalog.build(config.load(root))
        self.assertFalse([r for r in rows if "usage" in r["flows"]])

    def test_needs_include_conftest_secrets_and_the_stack(self) -> None:
        row = self.rows["it:connectors/jira/test_jira.py::TestJira::test_tc_incr_001_incremental"]
        self.assertIn("env:JIRA_TOKEN", row["needs"])
        self.assertIn("service:stack", row["needs"])

    def test_a_test_reaching_a_model_seeding_fixture_needs_a_model_key(self) -> None:
        llm = "env:TEST_OPENAI_API_KEY|TEST_AZURE_OPENAI_API_KEY+TEST_AZURE_OPENAI_ENDPOINT"
        testing.write(self.root, "integration-tests/agents/conftest.py", (
            "import pytest\n\n@pytest.fixture(scope='session')\ndef llm_model(client):\n    return setup_test_llm_model(client)\n\n"
            "@pytest.fixture\ndef agent(llm_model):\n    return llm_model\n"
        ))
        testing.write(self.root, "integration-tests/agents/test_agents.py", (
            "class TestAgents:\n    def test_chat(self, agent):\n        pass\n\n    def test_list(self, client):\n        pass\n"
        ))
        testing.write(self.root, "integration-tests/indexing/conftest.py", (
            "import pytest\n\n@pytest.fixture(autouse=True)\ndef _seeded(llm):\n    pass\n\n"
            "@pytest.fixture\ndef llm():\n    setup_test_llm_model(None)\n"
        ))
        testing.write(self.root, "integration-tests/indexing/test_index.py", "def test_indexed():\n    pass\n")
        config.clear_cache()
        rows = catalog.index(catalog.build(self.cfg))
        self.assertIn(llm, rows["it:agents/test_agents.py::TestAgents::test_chat"]["needs"])
        self.assertNotIn(llm, rows["it:agents/test_agents.py::TestAgents::test_list"]["needs"])
        self.assertIn(llm, rows["it:indexing/test_index.py::test_indexed"]["needs"])
        self.assertNotIn(llm, rows["it:retrieval/test_search.py::test_ranking"]["needs"])

    def test_unit_tests_are_listed_per_file(self) -> None:
        self.assertIn("py:tests/unit/services/test_retrieval.py", self.rows)
        self.assertIn("node:tests/modules/auth/auth.controller.test.ts", self.rows)

    def test_playwright_tags_are_read_from_titles(self) -> None:
        self.assertEqual(self.rows["e2e:tests/e2e/login.spec.ts"]["tags"], ["@smoke"])

    def test_overlay_files_belong_to_the_overlay_suite(self) -> None:
        row = self.rows["it:plus/test_ai_usage.py::test_budget"]
        self.assertEqual((row["suite"], row["edition"]), ("it-plus", "plus"))
        self.assertEqual(self.rows["py:plus/tests/unit/test_usage.py"]["suite"], "py-unit-plus")

    def test_overlay_paths_take_overlay_edition(self) -> None:
        for path, ed in (("backend/python/app/plus/x.py", "plus"), ("frontend/plus/a.tsx", "plus"),
                         ("backend/python/app/employee.py", "oss"), ("backend/python/app/edition.py", "switch")):
            self.assertEqual(self.cfg.edition(path), ed, path)
        self.assertEqual({r["edition"] for r in self.rows.values() if "/plus/" in r["path"] or r["path"].startswith("integration-tests/plus/")}, {"plus"})

    def test_overlay_row_in_base_suite_goes_to_overlay_catalog(self) -> None:
        testing.write(self.root, "integration-tests/plus/test_x.py", 'def test_x():\n    """TC-PLUS-X-001: x."""\n')
        testing.write(self.root, "integration-tests/retrieval/plus/test_y.py", 'def test_y():\n    """TC-PLUS-Y-001: y."""\n')
        config.clear_cache()
        outputs = catalog.committed_outputs(self.cfg)
        base = outputs[self.root / "integration-tests/catalog/tests.jsonl"]
        plus = outputs[self.root / "integration-tests/plus/catalog/tests.jsonl"]
        self.assertIn("test_y", plus)
        self.assertNotIn("test_y", base)

    def test_committed_base_catalog_has_no_overlay_rows(self) -> None:
        outputs = catalog.committed_outputs(self.cfg)
        oss = outputs[self.root / "integration-tests/catalog/tests.jsonl"]
        plus = outputs[self.root / "integration-tests/plus/catalog/tests.jsonl"]
        self.assertNotIn("plus/", oss)
        self.assertEqual([json.loads(line)["edition"] for line in plus.splitlines()], ["plus"])

    def test_rows_without_a_committed_home_are_counted_not_misfiled(self) -> None:
        testing.write(self.root, "integration-tests/retrieval/plus/test_y.py", 'def test_y():\n    """TC-PLUS-Y-001: y."""\n')
        config.clear_cache()
        cfg = config.load(self.root)
        cfg.suites = [s for s in cfg.suites if s["id"] != "it-plus"]
        rows = catalog.build(cfg, [s["id"] for s in catalog.committed_suites(cfg)])
        self.assertEqual(catalog.homeless(cfg, rows), sum(r["edition"] == "plus" for r in rows))
        self.assertGreaterEqual(catalog.homeless(cfg, rows), 1)
        self.assertNotIn("test_y", "".join(catalog.committed_outputs(cfg, rows).values()))

    def test_check_detects_a_stale_catalog(self) -> None:
        catalog.write(self.cfg)
        self.assertEqual(catalog.stale(self.cfg), [])
        testing.write(self.root, "integration-tests/retrieval/test_new.py", 'def test_x():\n    """TC-RET-003: new."""\n')
        config.clear_cache()
        self.assertTrue(catalog.stale(self.cfg))

    def test_lint_rejects_duplicate_tc_in_one_directory_only(self) -> None:
        testing.write(self.root, "integration-tests/retrieval/test_dupe.py", 'def test_y():\n    """TC-RET-001: duplicate."""\n')
        testing.write(self.root, "integration-tests/other/test_same_id.py", 'def test_z():\n    """TC-RET-001: same id, other area."""\n')
        config.clear_cache()
        errors, _ = catalog.lint(self.cfg, catalog.build(self.cfg))
        dupes = [e for e in errors if "duplicate" in e]
        self.assertEqual(len(dupes), 1)
        self.assertIn("integration-tests/retrieval", dupes[0])

    def test_lint_rejects_covers_that_match_nothing(self) -> None:
        testing.write(self.root, "integration-tests/retrieval/test_bad.py", 'def test_b():\n    """TC-RET-009: x.\n\n    covers: backend/python/app/nope/\n    """\n')
        config.clear_cache()
        errors, _ = catalog.lint(self.cfg, catalog.build(self.cfg))
        self.assertTrue(any("matches no file" in e for e in errors))

    def _lint_errors(self) -> list[str]:
        config.clear_cache()
        return catalog.lint(self.cfg, catalog.build(self.cfg))[0]

    def test_orphan_test_file_is_a_lint_error(self) -> None:
        testing.write(self.root, "tools/test_tool.py", "def test_t():\n    pass\n")
        self.assertTrue(any("tools/test_tool.py" in e and "belongs to no suite" in e for e in self._lint_errors()))

    def test_unowned_entry_silences_orphan(self) -> None:
        testing.write(self.root, "tools/test_tool.py", "def test_t():\n    pass\n")
        self.cfg.always["unowned"] = [{"paths": ["tools/**"], "reason": "manual"}]
        self.assertFalse(any("belongs to no suite" in e for e in self._lint_errors()))

    def test_unowned_entry_needs_a_reason(self) -> None:
        self.cfg.always["unowned"] = [{"paths": ["tools/**"]}]
        self.assertTrue(any("unowned" in e and "reason" in e for e in self._lint_errors()))

    def test_excluded_suite_path_is_an_orphan(self) -> None:
        testing.write(self.root, "integration-tests/unit/test_x.py", "def test_x():\n    pass\n")
        self.assertTrue(any("integration-tests/unit/test_x.py" in e for e in self._lint_errors()))


class TestOverlayIsOptional(unittest.TestCase):
    def test_oss_checkout_loads_without_overlay(self) -> None:
        root, tmp = testing.make_repo(overlay=False)
        self.addCleanup(tmp.cleanup)
        cfg = config.load(root)
        self.assertEqual(cfg.overlays, [])
        self.assertNotIn("py-unit-plus", [s["id"] for s in cfg.suites])

    def test_no_overlay_everything_is_base(self) -> None:
        root, tmp = testing.make_repo(overlay=False)
        self.addCleanup(tmp.cleanup)
        cfg = config.load(root)
        self.assertEqual(cfg.edition("plus/x.py"), "oss")
        self.assertEqual(cfg.editions(), ["all", "oss"])

    def test_bad_hook_raises(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        testing.write(root, "scripts/plus/qa/qa_hooks.py", 'raise ImportError("x")\n')
        with self.assertRaises(RuntimeError) as ctx:
            config.load(root)
        self.assertIn("qa_hooks.py", str(ctx.exception))


class TestPolicyRoot(unittest.TestCase):
    def test_rules_and_quarantine_come_from_the_engine_root(self) -> None:
        a, tmp_a = testing.make_repo()
        b, tmp_b = testing.make_repo()
        self.addCleanup(tmp_a.cleanup)
        self.addCleanup(tmp_b.cleanup)
        (b / "scripts/qa/quarantine.toml").write_text(
            '[[test]]\nid = "py:tests/a_test.py"\nreason = "flaky"\nowner = "x"\nexpires = "2999-01-01"\n', encoding="utf-8"
        )
        with (b / "scripts/qa/rules/impact.toml").open("a", encoding="utf-8") as f:
            f.write('\n[[rule]]\nid = "only-in-b"\npaths = ["b/**"]\n')
        cfg = config.load(root=b, engine_root=a)
        self.assertEqual(cfg.quarantined_ids(), set())
        self.assertNotIn("only-in-b", [r["id"] for r in cfg.rules])
        self.assertEqual(cfg.root, b.resolve())
        self.assertEqual(cfg.engine_root, a.resolve())

    def test_root_alone_keeps_reading_rules_from_it(self) -> None:
        b, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(config.load(root=b).engine_root, b.resolve())

    def test_qa_root_env_sets_the_tree(self) -> None:
        root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"QA_ROOT": str(root)}):
            self.assertEqual(paths.tree_root(), root)
        with mock.patch.dict(os.environ):
            os.environ.pop("QA_ROOT", None)
            self.assertEqual(paths.tree_root(), paths.REPO)


if __name__ == "__main__":
    unittest.main()
