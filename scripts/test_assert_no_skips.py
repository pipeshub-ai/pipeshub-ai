import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from assert_no_skips import collect, junit_id, main, shard_problems

XML = '<testsuites><testsuite><testcase classname="a.b" name="t1"/><testcase classname="a.b" name="t2"><skipped/></testcase></testsuite></testsuites>'


class AssertNoSkips(unittest.TestCase):
    def _report(self, text: str) -> str:
        d = Path(tempfile.mkdtemp())
        (d / "r.xml").write_text(text)
        return str(d / "r.xml")

    def test_unexpected_skip_fails_and_allow_list_passes(self) -> None:
        f = self._report(XML)
        self.assertEqual(main([f]), 1)
        self.assertEqual(main([f, "--allow", "a.b::t2"]), 0)

    def test_empty_run_fails(self) -> None:
        self.assertEqual(main([self._report("<testsuites/>")]), 1)

    def test_expect_suite_counts_cases_by_title_prefix_and_fails_on_a_failure(self) -> None:
        f = self._report(
            '<testsuites><testsuite><testcase classname="s one" name="a"/><testcase classname="s one two" name="b"/>'
            '<testcase classname="other" name="c"/></testsuite></testsuites>'
        )
        self.assertEqual(main([f, "--expect-suite", "s one=2"]), 0)
        self.assertEqual(main([f, "--expect-suite", "s one=3"]), 1)
        self.assertEqual(main([f, "--expect-suite", "missing=1"]), 1)
        # A suite with no cases is never a pass, so a count of 0 is refused.
        self.assertEqual(main([f, "--expect-suite", "missing=0"]), 1)
        self.assertEqual(main([f, "--expect-suite", "s one=0"]), 1)
        self.assertEqual(main([f, "--expect-suite", "s one"]), 1)
        broken = self._report('<testsuites><testsuite><testcase classname="s" name="a"><failure/></testcase></testsuite></testsuites>')
        self.assertEqual(main([broken, "--expect-suite", "s=1"]), 1)

    def test_failed_case_outside_expected_suites_is_named_and_fails(self) -> None:
        f = self._report(
            '<testsuites><testsuite><testcase classname="s" name="ok"/>'
            '<testcase classname="other" name="boom"><failure message="expected 1\nto equal 2"/></testcase></testsuite></testsuites>'
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([f, "--expect-suite", "s=1"]), 1)
        self.assertIn("FAILED other :: boom: expected 1", out.getvalue())


class Collected(unittest.TestCase):
    """--collect then --collected, on a real pytest run of a throwaway project, as the workflows use them."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "pytest.ini").write_text("[pytest]\naddopts = -q\n")  # the -q the backend's pytest.ini adds
        (self.dir / "tests").mkdir()
        self._file("test_a.py", "import pytest\n\n@pytest.mark.parametrize('x', ['a/b', 'c::d'])\ndef test_p(x): pass\n\n"
                   "class TestK:\n    def test_m(self): pass\n")
        self._file("test_b.py", "def test_b(): pass\n")

    def _file(self, name: str, text: str) -> str:
        (self.dir / "tests" / name).write_text(text)
        return f"tests/{name}"

    def _job(self, *args: str) -> int:
        """The workflow's step: collect, run with the same arguments, then check the report against the collection."""
        out, report = str(self.dir / "c.txt"), str(self.dir / "r.xml")
        with contextlib.redirect_stdout(io.StringIO()):
            ids, problem = collect(list(args), cwd=self.dir)
            if problem:
                return 1
            Path(out).write_text("".join(f"{i}\n" for i in ids))
            subprocess.run([sys.executable, "-m", "pytest", *args, f"--junitxml={report}"], check=False, cwd=self.dir, capture_output=True)
            return main([report, "--collected", out])

    def test_a_clean_run_passes(self) -> None:
        self.assertEqual(self._job("tests"), 0)
        ids, problem = collect(["tests"], cwd=self.dir)
        self.assertEqual((len(ids), problem), (4, ""))

    def test_a_skipped_test_fails(self) -> None:
        self._file("test_s.py", "import pytest\n\ndef test_s(): pytest.skip('no store')\n")
        self.assertEqual(self._job("tests"), 1)

    def test_a_file_that_does_not_import_fails(self) -> None:
        self._file("test_bad.py", "import no_such_module\n\ndef test_x(): pass\n")
        self.assertEqual(self._job("tests"), 1)
        _, problem = collect(["tests"], cwd=self.dir)
        self.assertIn("exited 2", problem)
        self.assertIn("no_such_module", problem)

    def test_a_named_file_with_no_selected_case_fails(self) -> None:
        empty = self._file("test_e.py", "def helper(): pass\n")
        self.assertIn(f"{empty}: no case selected", collect(["tests/test_b.py", empty], cwd=self.dir)[1])
        self.assertIn("test_b.py: no case selected", collect(["tests/test_b.py", "tests/test_a.py", "-k", "test_p"], cwd=self.dir)[1])
        # an ignored file is not a file the run names
        self.assertEqual(collect(["tests", "--ignore", "tests/test_b.py"], cwd=self.dir)[1], "")

    def test_nothing_collected_fails(self) -> None:
        self.assertIn("exited 5", collect(["tests", "-k", "nothing_matches"], cwd=self.dir)[1])

    def test_a_collected_case_that_did_not_run_or_a_case_not_collected_fails(self) -> None:
        ids, _ = collect(["tests"], cwd=self.dir)
        report = str(self.dir / "r.xml")
        subprocess.run([sys.executable, "-m", "pytest", "tests", "-k", "not test_b", f"--junitxml={report}"], check=False, cwd=self.dir, capture_output=True)
        listed = self.dir / "c.txt"
        listed.write_text("".join(f"{i}\n" for i in ids))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([report, "--collected", str(listed)]), 1)
        self.assertIn("NOT RUN tests.test_b::test_b", out.getvalue())
        listed.write_text("".join(f"{i}\n" for i in ids if "test_m" not in i))
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([report, "--collected", str(listed)]), 1)
        self.assertIn("NOT COLLECTED tests.test_a.TestK::test_m", out.getvalue())

    def test_the_cli_writes_the_node_ids(self) -> None:
        out = self.dir / "c.txt"
        script = Path(__file__).resolve().parent / "assert_no_skips.py"
        proc = subprocess.run([sys.executable, str(script), "--collect", str(out), "tests"], check=False, cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(len(out.read_text().splitlines()), 4)


class Ids(unittest.TestCase):
    def test_node_id_to_the_junit_id(self) -> None:
        self.assertEqual(junit_id("tests/x/test_a.py::test_f"), "tests.x.test_a::test_f")
        self.assertEqual(junit_id("tests/test_a.py::TestK::test_m[p]"), "tests.test_a.TestK::test_m[p]")
        self.assertEqual(junit_id("tests/test_a.py::test_p[a/b::c.py]"), "tests.test_a::test_p[a/b::c.py]")

    def test_shard_problems(self) -> None:
        self.assertEqual(shard_problems(["a", "b"], {1: ["a"], 2: ["b"]}), [])
        self.assertEqual(shard_problems(["a", "b"], {1: ["a"], 2: []}), ["in no shard: b"])
        self.assertEqual(shard_problems(["a"], {1: ["a"], 2: ["a"]}), ["in 2 shards: a"])
        self.assertEqual(shard_problems(["a"], {1: ["a", "z"]}), ["not in the folder: z"])


if __name__ == "__main__":
    unittest.main()
