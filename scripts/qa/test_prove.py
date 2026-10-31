"""qa prove: a test is proven only when it passes on head and fails on a revert or a mutant.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import ast
import contextlib
import io
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import catalog, config, prove, run, schema, testing

PROVIDER = "backend/python/app/services/graph_db/provider.py"
TEST_ID = "py:tests/unit/services/graph_db/test_provider.py"


def _snapshot(root: Path) -> tuple[list[str], dict[str, bytes]]:
    # config.load imports the overlay hook, which leaves bytecode; that is not an edit.
    status = [s for s in testing.git(root, "status", "--porcelain", "--untracked-files=all").splitlines() if "__pycache__" not in s]
    files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
             if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts}
    return status, files


class ProveCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        out = tempfile.TemporaryDirectory(prefix="qa-prove-out-")
        self.addCleanup(out.cleanup)
        self.out = Path(out.name)
        self.calls: list[tuple[str, str | None, dict]] = []

    def row(self) -> dict:
        config.clear_cache()
        row = dict(catalog.index(catalog.build(config.load(self.root)))[TEST_ID])
        row["covers"] = [PROVIDER]
        return row

    def prove(self, head: list[str], variant, **kw) -> dict:
        """head: outcomes for the runs in the user's checkout; variant(n, tree): outcome of the n-th variant run."""
        heads = list(head)

        def fake_run(cfg, plan, rows, out, **opts):
            tree = Path(os.environ["QA_ROOT"]).resolve()
            self.assertEqual(cfg.root, tree)
            self.calls.append((str(tree), plan.get("head"), opts))
            if tree == self.root.resolve():
                outcome, details = heads.pop(0), None
            else:
                outcome, details = variant(sum(1 for c in self.calls if c[0] != str(self.root.resolve())), tree)
            test = {"id": plan["tests"][0]["id"], "outcome": outcome, "seconds": 0.1, "details": details}
            return {"schema": "qa-results/v1", "tests": [test], "invocations": [], "fatal": []}

        before = _snapshot(self.root)
        cfg = config.load(self.root)
        try:
            with mock.patch.object(run, "run", fake_run), contextlib.redirect_stderr(io.StringIO()):
                return prove.prove(cfg, self.row(), self.out, **kw)
        finally:
            self.assertEqual(_snapshot(self.root), before)
            self.assertEqual(len(testing.git(self.root, "worktree", "list").splitlines()), 1)


def prove_with_mutant_kill(case: ProveCase) -> dict:
    """Head passes 3 times and the first variant (a mutant) fails."""
    def variant(n, tree):
        case.assertIn("return None", (tree / PROVIDER).read_text(encoding="utf-8"))
        return ("fail", "assert None == []")
    return case.prove(["pass", "pass", "pass"], variant)


class TestLint(unittest.TestCase):
    def test_lint_rejects_tests_that_only_check_no_raise(self) -> None:
        bad = {
            "test_a": "def test_a():\n    f()\n",
            "test_b": "def test_b():\n    assert True\n",
            "test_c": "import pytest\ndef test_c():\n    try:\n        f()\n    except Exception:\n        pytest.fail('raised')\n",
        }
        for name, src in bad.items():
            self.assertEqual(prove.assertion_problems(src),
                             [f"{name}: asserts nothing about behavior (only that the call did not raise)"], src)
        good = [
            "def test_d():\n    assert f() == 1\n",
            "import pytest\ndef test_e():\n    with pytest.raises(ValueError):\n        f()\n",
            "def test_f(m):\n    f()\n    m.assert_called_once_with(1)\n",
            "class TestG:\n    def helper(self):\n        pass\n    def test_g(self):\n        self.assertEqual(f(), 1)\n",
        ]
        for src in good:
            self.assertEqual(prove.assertion_problems(src), [], src)
        self.assertEqual(prove.assertion_problems("class TestH:\n    def test_h(self):\n        f()\n"),
                         ["test_h: asserts nothing about behavior (only that the call did not raise)"])


class TestMutants(unittest.TestCase):
    def test_mutants_are_deterministic_and_parse(self) -> None:
        src = "def f(x):\n    if x == 1:\n        return True\n    return []\n"
        got = prove.mutants(src)
        self.assertEqual([(line, op) for line, op, _ in got], [
            (2, "== -> !="), (2, "if -> if not"), (3, "True -> False"),
            (3, "return -> return None"), (4, "return -> return None")])
        for _, _, mutated in got:
            ast.parse(mutated)
            self.assertNotEqual(mutated, ast.unparse(ast.parse(src)))
        self.assertEqual(got, prove.mutants(src))
        self.assertEqual([(line, op) for line, op, _ in prove.mutants(src, {4})], [(4, "return -> return None")])
        self.assertEqual(prove.mutants("def g():\n    return None\n"), [])


class TestProve(ProveCase):
    def test_prove_passes_when_a_mutant_fails(self) -> None:
        result = prove_with_mutant_kill(self)
        self.assertEqual(result["verdict"], "proven")
        self.assertEqual(result["reason"], f"fails under mutation return -> return None at {PROVIDER}:2")
        self.assertEqual(result["head_runs"], ["pass", "pass", "pass"])
        self.assertIsNone(result["revert"])
        self.assertEqual(result["mutants"], [{"file": PROVIDER, "line": 2, "op": "return -> return None", "outcome": "fail"}])
        evidence = self.out / "prove" / "py_tests_unit_services_graph_db_test_provider.py.json"
        self.assertTrue(evidence.is_file())
        self.assertEqual(schema.check(result, "proof.v1.json"), [])
        heads = [h for _, h, _ in self.calls]
        sha = testing.git(self.root, "rev-parse", "HEAD")
        self.assertEqual(heads, [f"{sha[:8]}h"] * 3 + [f"{sha[:8]}v1"])
        self.assertEqual(result["head"], sha)
        self.assertTrue(all(opts["retry"] is False for _, _, opts in self.calls))

    def test_prove_flaky_head_is_unproven(self) -> None:
        result = self.prove(["pass", "fail", "pass"], lambda n, tree: self.fail("no variant may run"))
        self.assertEqual(result["verdict"], "unproven")
        self.assertEqual(result["reason"], "did not pass on head: pass, fail, pass")
        self.assertEqual(len(self.calls), 3)

    def test_prove_lint_problem_is_unproven_without_runs(self) -> None:
        testing.write(self.root, "backend/python/tests/unit/services/graph_db/test_provider.py",
                      "from app.services.graph_db import provider\n\n\ndef test_records():\n    provider.get_records()\n")
        testing.commit(self.root, "weak test")
        result = self.prove([], lambda n, tree: self.fail("no run"))
        self.assertEqual((result["verdict"], result["head_runs"]), ("unproven", []))
        self.assertEqual(len(result["lint"]), 1)
        self.assertEqual(self.calls, [])

    def test_prove_not_run_variant_is_inconclusive(self) -> None:
        result = self.prove(["pass"] * 3, lambda n, tree: ("not_run", "infra: no stack"))
        self.assertEqual(result["verdict"], "unproven")
        self.assertTrue(result["reason"].startswith("inconclusive"), result["reason"])
        self.assertEqual(result["reason"], "inconclusive: 1 variants not run (infra)")

    def test_prove_prefers_the_revert_when_base_is_given(self) -> None:
        parent = testing.git(self.root, "rev-parse", "HEAD")
        original = (self.root / PROVIDER).read_text(encoding="utf-8")
        testing.write(self.root, PROVIDER, "def get_records():\n    return [1]\n")
        testing.commit(self.root, "change")

        def variant(n, tree):
            self.assertEqual((tree / PROVIDER).read_text(encoding="utf-8"), original)
            return ("fail", None)
        result = self.prove(["pass"] * 3, variant, base=parent)
        self.assertEqual(result["verdict"], "proven")
        self.assertEqual(result["reason"], "fails when the change is reverted")
        self.assertEqual(result["revert"], {"base": parent, "files": [PROVIDER], "outcome": "fail"})
        self.assertEqual(result["mutants"], [])

    def test_prove_sees_uncommitted_and_untracked_files(self) -> None:
        testing.write(self.root, PROVIDER, "def get_records():\n    return [2]\n")
        new_test = "backend/python/tests/unit/services/graph_db/test_new.py"
        testing.write(self.root, new_test, "def test_new():\n    assert 1 == 1\n")

        def variant(n, tree):
            self.assertTrue((tree / new_test).is_file())
            self.assertIn("return None", (tree / PROVIDER).read_text(encoding="utf-8"))
            return ("pass", None)
        row = {**self.row(), "path": new_test}
        with mock.patch.object(self, "row", lambda: row):
            result = self.prove(["pass"] * 3, variant)
        self.assertEqual(result["reason"], "no revert or mutation made it fail")

    def test_prove_typescript_covers_have_nothing_to_mutate(self) -> None:
        row = self.row()
        row["covers"] = ["backend/nodejs/apps/src/x.ts"]
        with mock.patch.object(self, "row", lambda: row):
            result = self.prove(["pass"] * 3, lambda n, tree: self.fail("no variant"))
        self.assertEqual((result["verdict"], result["reason"]), ("unproven", "no Python source to mutate"))

    def test_prove_removes_its_worktrees_when_a_run_raises(self) -> None:
        def variant(n, tree):
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.prove(["pass"] * 3, variant)
        self.assertEqual(len(testing.git(self.root, "worktree", "list").splitlines()), 1)

    def test_prove_removes_its_worktrees_on_sigterm(self) -> None:
        def variant(n, tree):
            os.kill(os.getpid(), signal.SIGTERM)
            return ("pass", None)
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(run._Cancelled):
            self.prove(["pass"] * 3, variant)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_managed_head_runs_never_use_the_qa_run_image(self) -> None:
        from qa import stack
        self.prove(["pass"] * 3, lambda n, tree: ("fail", None), stack_mode="managed")
        sha = testing.git(self.root, "rev-parse", "HEAD")
        tags = {stack.image_tag(h) for _, h, _ in self.calls}
        self.assertNotIn(stack.image_tag(sha), tags)
        self.assertEqual(len(tags), 2)
        self.assertTrue(all(opts["build"] for _, _, opts in self.calls))

    def test_unparsable_mutant_is_never_a_kill(self) -> None:
        with mock.patch.object(prove, "mutants", lambda src, lines=None: [(2, "== -> !=", "def (:\n")]):
            result = self.prove(["pass"] * 3, lambda n, tree: self.fail("an unparsable mutant must not run"))
        self.assertEqual(result["mutants"], [{"file": PROVIDER, "line": 2, "op": "== -> !=", "outcome": "not_run",
                                              "details": "mutant does not parse"}])
        self.assertEqual(result["reason"], "inconclusive: 1 variants not run (unparsable mutant)")
        self.assertEqual(schema.check(result, "proof.v1.json"), [])

    def test_collection_error_in_a_variant_is_inconclusive(self) -> None:
        result = self.prove(["pass"] * 3, lambda n, tree: (
            "error", "no result in JUnit (exit 2); collection error or not collected"))
        self.assertEqual(result["verdict"], "unproven")
        self.assertEqual(result["reason"], "inconclusive: 1 variants not run (early error)")
        self.assertEqual(result["mutants"][0]["outcome"], "not_run")
        self.assertTrue(result["mutants"][0]["details"].startswith("error before any test ran"))

    def test_runtime_error_in_a_variant_still_kills(self) -> None:
        result = self.prove(["pass"] * 3, lambda n, tree: ("error", "test_records: TypeError: NoneType"))
        self.assertEqual(result["verdict"], "proven")

    def _lint_row(self, target: str) -> dict:
        testing.write(self.root, "backend/python/tests/unit/services/graph_db/test_provider.py",
                      "from app.services.graph_db import provider\n\n\ndef test_weak():\n    provider.get_records()\n\n\n"
                      "class TestP:\n    def test_good(self):\n        assert provider.get_records() == []\n\n"
                      "    def test_weak(self):\n        provider.get_records()\n")
        testing.commit(self.root, "mixed tests")
        return {**self.row(), "id": f"{TEST_ID}::{target}"}

    def test_lint_scopes_to_the_named_function(self) -> None:
        row = self._lint_row("TestP::test_good")
        with mock.patch.object(self, "row", lambda: row):
            result = self.prove(["pass"] * 3, lambda n, tree: ("fail", None))
        self.assertEqual((result["lint"], result["verdict"]), ([], "proven"))

    def test_lint_blocks_a_weak_target_function(self) -> None:
        for target in ("test_weak", "TestP::test_weak"):
            self.calls.clear()
            row = self._lint_row(target)
            with mock.patch.object(self, "row", lambda: row):
                result = self.prove([], lambda n, tree: self.fail("no run"))
            self.assertEqual(result["lint"], ["test_weak: asserts nothing about behavior (only that the call did not raise)"], target)
            self.assertEqual(self.calls, [])

    def test_stack_rows_default_to_one_run_and_one_mutant(self) -> None:
        row = self.row()
        row["needs"] = ["service:stack"]
        with mock.patch.object(self, "row", lambda: row):
            result = self.prove(["pass"], lambda n, tree: ("pass", None), stack_mode="managed")
        self.assertEqual(result["head_runs"], ["pass"])
        self.assertTrue(all(opts["build"] for _, _, opts in self.calls))
        self.assertEqual(len(result["mutants"]), 1)


class TestProveCommand(ProveCase):
    def test_unknown_id_exits_2(self) -> None:
        from qa import __main__ as qa_main
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), mock.patch.object(prove, "prove") as p, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            config.clear_cache()
            self.assertEqual(qa_main.main(["--out", str(self.out), "prove", "py:nope.py"]), 2)
            p.return_value = {"verdict": "proven", "reason": "fails when the change is reverted"}
            self.assertEqual(qa_main.main(["--out", str(self.out), "prove", TEST_ID, "--repeat", "2"]), 0)
            self.assertEqual(p.call_args.kwargs["repeat"], 2)
            p.return_value = {"verdict": "unproven", "reason": "x"}
            self.assertEqual(qa_main.main(["--out", str(self.out), "prove", TEST_ID]), 1)


if __name__ == "__main__":
    unittest.main()
