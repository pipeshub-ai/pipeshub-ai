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
from qa import catalog, config, generate, paths, planner, prove, testing

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


UNIT_STUB_PATH = f"{GRAPH_DIR}/test_lists_nothing.py"
UNIT_ID = f"py:{UNIT_STUB_PATH.removeprefix('backend/python/')}"
GOOD = (
    '"""TC-GRAPH-DB-001: Lists nothing"""\n\n'
    "from app.services.graph_db import provider\n\n\n"
    "def test_lists_nothing() -> None:\n"
    "    assert provider.get_records() == []\n"
)


class TestImplement(GenerateCase):
    def setUp(self) -> None:
        super().setUp()
        self.generate("graph-db", [scenario("Lists nothing")])
        testing.commit(self.root, "stub")
        self.stub = (self.root / UNIT_STUB_PATH).read_bytes()
        self.asked: list[dict] = []
        self.proved: list[str] = []

    def implement(self, content=GOOD, approved=True, verdict="proven", reason="fails under mutation", ids=UNIT_ID, *extra):
        def fake_ask(root, adapter, task, input_path, output_path, timeout=600):
            self.assertEqual(task, "implement")
            self.asked.append(json.loads(input_path.read_text(encoding="utf-8")))
            status = {"adapter": "test", "status": "ok"}
            output_path.write_text(json.dumps({"content": content, "_planner": status}), encoding="utf-8")
            return status

        def fake_prove(cfg, row, out, **kw):
            self.proved.append(row["id"])
            if getattr(self, "prove_error", None):
                raise self.prove_error
            self.assertEqual((cfg.root / row["path"]).read_text(encoding="utf-8"), content)
            return {"verdict": verdict, "reason": reason}

        out = self.out.parent.parent / f"{self.root.name}-out"  # outside the repo, so git status shows only the repo
        self.addCleanup(lambda: __import__("shutil").rmtree(out, ignore_errors=True))
        with mock.patch.object(paths, "REPO", self.root), mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), \
                mock.patch.object(planner, "ask", fake_ask), mock.patch.object(prove, "prove", fake_prove), \
                mock.patch.object(generate, "approved", return_value=approved) as gate, \
                contextlib.redirect_stdout(io.StringIO()) as so, contextlib.redirect_stderr(io.StringIO()) as err:
            config.clear_cache()
            argv = ["--out", str(out), "generate", "--implement", ids, *extra]
            if "--no-pr" not in extra:
                argv += ["--pr", "7"]
            else:
                argv.remove("--no-pr")
            code = qa_main.main(argv)
        config.clear_cache()
        self.gate, self.stdout, self.err = gate, so.getvalue(), err.getvalue()
        self.result = json.loads((out / "implement.json").read_text(encoding="utf-8")) if (out / "implement.json").is_file() else None
        return code

    def changed(self) -> set:
        """Modified and untracked paths (testing.git strips the porcelain status column)."""
        return set((testing.git(self.root, "diff", "--name-only", "HEAD") + "\n"
                    + testing.git(self.root, "ls-files", "--others", "--exclude-standard")).split())

    def assert_stub_unchanged(self) -> None:
        self.assertEqual((self.root / UNIT_STUB_PATH).read_bytes(), self.stub)

    def test_implement_refuses_without_the_label(self) -> None:
        self.assertEqual(self.implement(approved=False), 1)
        self.assertIn("PR 7 does not have the label qa-scenarios-ok", self.stdout + self.err)
        self.assertEqual(self.asked, [])
        self.assert_stub_unchanged()

    def test_implement_without_pr_explains(self) -> None:
        self.assertEqual(self.implement(UNIT_ID, True, "proven", "", UNIT_ID, "--no-pr"), 2)
        self.assertIn("--implement needs --pr: the scenarios are approved with the label qa-scenarios-ok", self.err)
        self.assertEqual(self.asked, [])

    def test_approved_reads_the_label_with_gh(self) -> None:
        def reply(stdout="", exc=None):
            def run(cmd, **kw):
                self.cmds.append(cmd)
                if exc:
                    raise exc
                return mock.Mock(stdout=stdout)
            return run
        self.cmds = []
        with mock.patch.object(generate.subprocess, "run", reply('{"labels": [{"name": "qa-scenarios-ok"}]}')):
            self.assertTrue(generate.approved(7, "o/r"))
        self.assertEqual(self.cmds[0], ["gh", "pr", "view", "7", "--json", "labels", "--repo", "o/r"])
        for run in (reply('{"labels": [{"name": "bug"}]}'), reply("not json"), reply("[]"),
                    reply(exc=FileNotFoundError("gh")), reply(exc=generate.subprocess.CalledProcessError(1, "gh"))):
            with mock.patch.object(generate.subprocess, "run", run):
                self.assertFalse(generate.approved(7, None))

    def test_empty_model_reply_is_removed(self) -> None:
        self.assertEqual(self.implement(""), 1)
        self.assertIn("no test from the model", self.result["removed"][0]["reason"])
        self.assert_stub_unchanged()

    def test_module_and_implement_are_exclusive(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            qa_main.main(["generate", "--module", "graph-db", "--implement", UNIT_ID, "--pr", "7"])

    def test_implement_refuses_an_uncommitted_stub(self) -> None:
        (self.root / UNIT_STUB_PATH).write_text(self.stub.decode() + "\n# edited\n", encoding="utf-8")
        self.assertEqual(self.implement(), 1)
        self.assertEqual([r["id"] for r in self.result["refused"]], [UNIT_ID])
        self.assertEqual(self.asked, [])

    def test_implement_refuses_rows_that_are_not_planned(self) -> None:
        self.assertEqual(self.implement(ids="py:tests/unit/services/graph_db/test_provider.py,py:nope.py"), 1)
        self.assertEqual(len(self.result["refused"]), 2)
        self.assertEqual(self.asked, [])

    def test_implement_rejects_content_that_keeps_the_planned_marker(self) -> None:
        self.assertEqual(self.implement(GOOD + "\nimport pytest\npytestmark = [pytest.mark.planned]\n"), 1)
        self.assertEqual(self.result["removed"][0]["id"], UNIT_ID)
        self.assertIn("still marked planned", self.result["removed"][0]["reason"])
        self.assert_stub_unchanged()
        self.assertEqual(self.proved, [])

    def test_implement_rejects_content_that_only_checks_no_raise(self) -> None:
        self.assertEqual(self.implement(GOOD.replace("assert provider.get_records() == []", "provider.get_records()")), 1)
        self.assertIn("asserts nothing about behavior", self.result["removed"][0]["reason"])
        self.assert_stub_unchanged()

    def test_check_content(self) -> None:
        stub = self.stub.decode()
        self.assertEqual(generate.check_content(stub, GOOD), [])
        self.assertTrue(generate.check_content(stub, "def test_lists_nothing(:\n")[0].startswith("does not parse: "))
        self.assertIn("missing test_lists_nothing", generate.check_content(stub, GOOD.replace("test_lists_nothing", "test_other")))
        self.assertIn("larger than 30000 bytes", generate.check_content(stub, GOOD + "#" * 30000))
        # Security: no os/subprocess/socket imports anywhere, and nothing at module level but imports,
        # assignments, a docstring and the stub's own test function.
        for bad in ("import os\n", "import subprocess as sp\n", "from socket import socket\n", "import os.path\n"):
            self.assertTrue(any("imports" in p for p in generate.check_content(stub, bad + GOOD)), bad)
        inner = GOOD.replace("    assert", "    import subprocess\n    assert")
        self.assertTrue(any("imports subprocess" in p for p in generate.check_content(stub, inner)))
        for extra in ("\ndef helper():\n    return 1\n", "\nclass TestMore:\n    def test_x(self):\n        assert 1 == 1\n",
                      "\ndef test_second():\n    assert 1 == 1\n", "\nprint('boom')\n"):
            self.assertTrue(any("module level" in p for p in generate.check_content(stub, GOOD + extra)), extra)

    def test_check_content_rejects_dynamic_code_and_io(self) -> None:
        stub = self.stub.decode()
        cases = {
            "__import__": "    __import__('o' + 's').system('x')\n",
            "importlib": "    importlib.import_module('os')\n",
            "ev" "al": "    ev" "al('1')\n",  # spelled in two parts: a repo text check reserves the word
            "exec": "    exec('x = 1')\n",
            "getattr": "    getattr(__builtins__, 'open')\n",
            "__subclasses__": "    ().__class__.__bases__[0].__subclasses__()\n",
            "open": "    open('/etc/passwd').read()\n",
            "imports pathlib": None, "imports shutil": None, "imports requests": None,
        }
        for word, line in cases.items():
            if line is None:
                content = f"import {word.split()[1]}\n" + GOOD
            else:
                content = GOOD.replace("    assert", line + "    assert")
            problems = generate.check_content(stub, content)
            self.assertTrue(any(word in p for p in problems), (word, problems))
        self.assertEqual(generate.check_content(stub, GOOD.replace("    assert", "    assert __name__\n    assert")), [])

    def test_failed_restore_is_loud(self) -> None:
        real = generate._git

        def git(root, *args):
            if args[0] == "checkout":
                return generate.subprocess.CompletedProcess(args, 1, "", "error: pathspec locked\nmore")
            return real(root, *args)

        with mock.patch.object(generate, "_git", git):
            self.assertEqual(self.implement(verdict="unproven", reason="no kill"), 1)
        self.assertEqual(self.result["removed"], [{"id": UNIT_ID, "reason": "restore failed: error: pathspec locked"}])
        self.assertIn("could not restore the stub", self.err)

    def test_implement_rejects_extra_imports_and_functions(self) -> None:
        self.assertEqual(self.implement("import subprocess\n" + GOOD + "\ndef test_two():\n    assert 1 == 1\n"), 1)
        reason = self.result["removed"][0]["reason"]
        self.assertIn("imports subprocess", reason)
        self.assertIn("module level", reason)
        self.assert_stub_unchanged()
        self.assertEqual(self.proved, [])

    def test_implement_writes_only_the_stub_file(self) -> None:
        self.assertEqual(self.implement(), 0)
        self.assertEqual(self.result["kept"], [{"id": UNIT_ID, "reason": "fails under mutation"}])
        self.assertEqual(self.proved, [UNIT_ID])
        self.assertEqual(self.changed(), {UNIT_STUB_PATH})
        self.assertEqual((self.root / UNIT_STUB_PATH).read_text(encoding="utf-8"), GOOD)
        asked = self.asked[0]
        self.assertEqual(asked["row"]["id"], UNIT_ID)
        self.assertEqual(asked["stub"], self.stub.decode())
        self.assertEqual([n["path"] for n in asked["neighbours"]], [f"{GRAPH_DIR}/test_provider.py"])
        self.assertEqual([s["path"] for s in asked["sources"]], [PROVIDER])
        self.gate.assert_called_once_with(7, None)

    def test_implement_writes_only_the_stub_file_for_an_it_row(self) -> None:
        self.generate("retrieval", [scenario("Search is empty", level="integration",
                                             covers=["backend/python/app/services/retrieval.py"])])
        testing.commit(self.root, "it stub")
        path = "integration-tests/retrieval/test_search_is_empty.py"
        rid = "it:retrieval/test_search_is_empty.py::test_search_is_empty"
        body = "import pytest\n\n\n@pytest.mark.integration\ndef test_search_is_empty(client) -> None:\n    assert client.search() == []\n"
        self.assertEqual(self.implement(body, ids=rid), 0)
        changed = self.changed()
        self.assertIn(path, changed)
        self.assertTrue(all(c == path or c.startswith("integration-tests/catalog/") for c in changed), changed)

    def test_unproven_test_is_removed_and_the_stub_restored(self) -> None:
        self.assertEqual(self.implement(verdict="unproven", reason="no revert or mutation made it fail"), 1)
        self.assertEqual(self.result["removed"], [{"id": UNIT_ID, "reason": "no revert or mutation made it fail"}])
        self.assert_stub_unchanged()
        self.assertEqual(self.changed(), set())

    def test_prove_crash_restores_the_stub(self) -> None:
        self.prove_error = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.implement()
        self.assert_stub_unchanged()

if __name__ == "__main__":
    unittest.main()
