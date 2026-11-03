import tempfile
import unittest
from pathlib import Path

from assert_no_skips import main

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


if __name__ == "__main__":
    unittest.main()
