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


if __name__ == "__main__":
    unittest.main()
