"""new_lint_findings.py lints a large pull request in runs small enough for one exec argument.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("new_lint_findings", REPO / ".github" / "scripts" / "new_lint_findings.py")
new_lint_findings = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(new_lint_findings)

# Linux's MAX_ARG_STRLEN: npx joins its arguments into one `sh -c` string, which may not exceed this.
MAX_ARG_STRLEN = 128 * 1024


def paths(count: int) -> list[str]:
    return [f"tests/modules/agent-harness/projection/some-long-test-file-name-{i:05d}.test.ts" for i in range(count)]


class TestBatches(unittest.TestCase):
    def test_every_run_fits_one_exec_argument_and_keeps_every_path_in_order(self) -> None:
        files = paths(2600)
        runs = new_lint_findings.batches(files)
        self.assertGreater(len(runs), 1)
        self.assertEqual([f for run in runs for f in run], files)
        for run in runs:
            self.assertLess(len(" ".join(run)), MAX_ARG_STRLEN)

    def test_a_small_change_is_one_run(self) -> None:
        self.assertEqual(new_lint_findings.batches(paths(3)), [paths(3)])
        self.assertEqual(new_lint_findings.batches([]), [])

    def test_lint_reports_the_findings_of_every_run(self) -> None:
        files = [os.path.join("backend/nodejs/apps", f) for f in paths(2600)]
        calls = []

        def fake_run(tool, cwd, rel):
            calls.append(rel)
            return [{"filePath": os.path.join(cwd, rel[0]), "messages": [{"severity": 2, "ruleId": "no-x", "line": 1}]}]

        with mock.patch.object(new_lint_findings, "_run", fake_run):
            found = new_lint_findings.lint("eslint", files)
        self.assertGreater(len(calls), 1)
        self.assertEqual(len(found), len(calls))
        self.assertEqual({f for f, _, _ in found}, {os.path.join("backend/nodejs/apps", run[0]) for run in calls})


if __name__ == "__main__":
    unittest.main()
