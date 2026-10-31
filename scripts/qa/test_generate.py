"""qa generate: model scenarios become planned stubs, written only by the engine.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import ast
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from qa import __main__ as qa_main
from qa import catalog, config, generate, paths, planner, testing

PROVIDER = "backend/python/app/services/graph_db/provider.py"
GRAPH_DIR = "backend/python/tests/unit/services/graph_db"


def scenario(title, level="unit", covers=(PROVIDER,), steps=("list records",), expected=("an empty list",), data=()):
    return {"title": title, "level": level, "steps": list(steps), "expected": list(expected),
            "data": list(data), "covers": list(covers)}


class GenerateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.out = self.root / "qa-out"

    def generate(self, module: str, scenarios: list[dict], *extra: str) -> int:
        def fake_ask(root, adapter, task, input_path, output_path, timeout=600):
            self.assertEqual(task, "scenarios")
            self.input = json.loads(input_path.read_text(encoding="utf-8"))
            status = {"adapter": "test", "status": "ok"}
            output_path.write_text(json.dumps({"scenarios": scenarios, "_planner": status}), encoding="utf-8")
            return status

        with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
                mock.patch.object(planner, "ask", fake_ask), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            config.clear_cache()
            code = qa_main.main(["--out", str(self.out), "generate", "--module", module, *extra])
        self.err = err.getvalue()
        config.clear_cache()
        return code

    def rows(self) -> dict:
        config.clear_cache()
        return catalog.index(catalog.build(config.load(self.root)))

    def report(self) -> str:
        return (self.out / "generate.md").read_text(encoding="utf-8")


class TestGenerate(GenerateCase):
    def test_helpers(self) -> None:
        self.assertEqual(generate.tc_area("graph-db"), "GRAPH-DB")
        self.assertEqual(generate.slug("  Lists Nothing, when EMPTY!! "), "lists_nothing_when_empty")
        self.assertEqual(len(generate.slug("a" * 80)), 50)
        self.assertEqual(generate.doc_safe('say "hi" \\ @me'), "say 'hi' / me")

    def test_scenarios_become_planned_stubs_in_the_module_home(self) -> None:
        self.assertEqual(self.generate("graph-db", [scenario("Lists nothing when empty")]), 0)
        path = f"{GRAPH_DIR}/test_lists_nothing_when_empty.py"
        row = self.rows()[f"py:{path.removeprefix('backend/python/')}"]
        self.assertEqual((row["status"], row["tc"], row["title"]), ("planned", "TC-GRAPH-DB-001", "Lists nothing when empty"))
        self.assertEqual(row["covers"], [PROVIDER])
        self.assertEqual(self.input["module"]["id"], "graph-db")
        self.assertIn(PROVIDER, self.input["sources"])
        self.assertNotIn(f"{GRAPH_DIR}/test_provider.py", self.input["sources"])

        self.assertEqual(self.generate("retrieval", [scenario(
            "Search returns nothing for an empty graph", level="integration",
            covers=["backend/python/app/services/retrieval.py"])]), 0)
        it = "integration-tests/retrieval/test_search_returns_nothing_for_an_empty_graph.py"
        row = self.rows()[f"it:{it.removeprefix('integration-tests/')}::test_search_returns_nothing_for_an_empty_graph"]
        self.assertEqual((row["status"], row["tc"]), ("planned", "TC-RETRIEVAL-001"))
        self.assertIn("integration", row["markers"])
        self.assertIn("TC-RETRIEVAL-001", self.report())
        self.assertIn("qa-scenarios-ok", self.report())
        # The committed it-stack catalog lists the new planned row.
        self.assertIn("test_search_returns_nothing_for_an_empty_graph",
                      (self.root / "integration-tests/catalog/tests.jsonl").read_text(encoding="utf-8"))

    def test_scenario_text_cannot_break_out_of_the_docstring(self) -> None:
        title = 'x"""\nimport os; os.system("boom")\n"""'
        self.assertEqual(self.generate("graph-db", [scenario(title, steps=['a\\"""b'])]), 0)
        written = [p for p in (self.root / GRAPH_DIR).glob("test_*.py") if p.name != "test_provider.py"]
        self.assertEqual(len(written), 1)
        tree = ast.parse(written[0].read_text(encoding="utf-8"))
        self.assertEqual([type(n).__name__ for n in tree.body], ["Expr", "Import", "Assign", "FunctionDef"])
        body = tree.body[-1].body
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0], ast.Expr)
        self.assertIs(body[0].value.value, Ellipsis)
        self.assertNotIn("\n", ast.get_docstring(tree).splitlines()[0])

    def test_generate_never_overwrites_and_skips_duplicate_titles(self) -> None:
        reply = [scenario("Empty list"), scenario("Empty-list"), scenario("Search hides records the user cannot read")]
        self.generate("graph-db", reply)
        self.assertTrue((self.root / GRAPH_DIR / "test_empty_list.py").is_file())
        self.assertTrue((self.root / GRAPH_DIR / "test_empty_list_2.py").is_file())
        first = (self.root / GRAPH_DIR / "test_empty_list.py").read_text(encoding="utf-8")
        self.assertIn("TC-GRAPH-DB-002", (self.root / GRAPH_DIR / "test_empty_list_2.py").read_text(encoding="utf-8"))

        self.generate("graph-db", reply + [scenario("Empty  list!")])
        self.assertEqual((self.root / GRAPH_DIR / "test_empty_list.py").read_text(encoding="utf-8"), first)
        self.assertTrue((self.root / GRAPH_DIR / "test_empty_list_3.py").is_file())
        self.assertFalse((self.root / GRAPH_DIR / "test_empty_list_4.py").exists())
        self.assertIn("Empty-list: duplicate title", self.report())
        self.assertIn("Empty list: duplicate title", self.report())

    def test_title_of_an_existing_row_is_a_duplicate(self) -> None:
        self.generate("retrieval", [scenario("search hides records THE USER cannot read.", level="integration",
                                             covers=["backend/python/app/services/retrieval.py"])])
        self.assertIn("duplicate title", self.report())
        self.assertEqual(sorted(p.name for p in (self.root / "integration-tests/retrieval").glob("test_*.py")), ["test_search.py"])

    def test_e2e_scenarios_are_dropped_with_a_reason(self) -> None:
        self.generate("graph-db", [scenario("Log in and see records", level="e2e")])
        self.assertIn("e2e stubs are not supported yet", self.report())
        self.assertEqual([p.name for p in (self.root / GRAPH_DIR).glob("test_*.py")], ["test_provider.py"])

    def test_covers_outside_the_module_are_dropped(self) -> None:
        self.generate("graph-db", [
            scenario("Only outside", covers=["backend/python/app/services/retrieval.py"]),
            scenario("Mixed covers", covers=["backend/nodejs/apps/src/modules/auth/auth.service.ts", PROVIDER]),
        ])
        self.assertIn("Only outside: covers outside the module", self.report())
        row = self.rows()["py:tests/unit/services/graph_db/test_mixed_covers.py"]
        self.assertEqual(row["covers"], [PROVIDER])

    def test_unit_scenario_with_no_python_cover_has_no_home(self) -> None:
        self.generate("graph-db", [scenario("No covers", covers=[])])
        self.assertIn("No covers: no unit home:", self.report())

    def written(self) -> list:
        return [p.resolve() for p in self.root.rglob("test_*.py")
                if not testing.git(self.root, "ls-files", str(p.relative_to(self.root)))]

    def test_traversal_absolute_and_glob_covers_are_dropped(self) -> None:
        outside = self.root.parent / "escape"
        bad = [
            "backend/python/app/services/graph_db/../../../../../../escape/x.py",
            "backend/python/app/services/graph_db/./provider.py",
            str(self.root / PROVIDER),
            "/" + PROVIDER,
            "backend/python/app/services/graph_db/*.py",
            "backend/python/app/services/graph_db/prov?der.py",
            "backend\\python/app/services/graph_db/provider.py",
        ]
        self.generate("graph-db", [scenario(f"Bad cover {i}", covers=[c]) for i, c in enumerate(bad)], "--max", "20")
        for i in range(len(bad)):
            self.assertIn(f"Bad cover {i}: covers outside the module", self.report())
        self.assertEqual(self.written(), [])
        self.assertFalse(outside.exists())
        self.assertTrue(all(generate.plain_path(c) is False for c in bad))
        self.assertTrue(generate.plain_path(PROVIDER))

    def test_nothing_is_written_outside_the_suite_dir(self) -> None:
        escape = "backend/python/tests/unit/../../../../escape/test_x.py"
        with mock.patch.object(generate, "home", return_value=(escape, "forced")):
            self.generate("graph-db", [scenario("Forced escape")])
        self.assertIn("Forced escape: unsafe path", self.report())
        self.assertEqual(self.written(), [])
        self.generate("graph-db", [scenario("Lists nothing"), scenario("Search works", level="integration", covers=[])])
        suite_dirs = [(self.root / d).resolve() for d in ("backend/python/tests/unit", "integration-tests")]
        got = self.written()
        self.assertEqual(len(got), 2)
        for p in got:
            self.assertTrue(any(p.is_relative_to(d) for d in suite_dirs), p)

    def test_empty_title_is_dropped(self) -> None:
        self.generate("graph-db", [scenario(""), scenario("!!! ---"), scenario("\u00e9\u00e9")])
        self.assertEqual(self.report().count("empty title"), 3)
        self.assertEqual(self.written(), [])

    def test_read_rows_reads_one_planned_file(self) -> None:
        self.generate("graph-db", [scenario("Lists nothing")])
        cfg = config.load(self.root)
        path = f"{GRAPH_DIR}/test_lists_nothing.py"
        rows = catalog.read_rows(cfg, path)
        self.assertEqual([(r["id"], r["status"]) for r in rows], [(f"py:{path.removeprefix('backend/python/')}", "planned")])
        self.assertEqual(catalog.read_rows(cfg, "frontend/tests/e2e/login.spec.ts"), [])

    def test_unknown_module_is_an_error(self) -> None:
        self.assertEqual(self.generate("nope", []), 2)
        self.assertIn("graph-db", self.err)

    def test_stub_that_does_not_parse_as_planned_is_removed(self) -> None:
        with mock.patch.object(generate, "UNIT_STUB", "def test_{slug}():\n    pass\n"):
            self.generate("graph-db", [scenario("Broken template")])
        self.assertIn("stub did not parse as planned", self.report())
        self.assertFalse((self.root / GRAPH_DIR / "test_broken_template.py").exists())


if __name__ == "__main__":
    unittest.main()
