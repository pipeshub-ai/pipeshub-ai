"""The licence check denies AGPL, GPL, SSPL and BUSL, reads expressions as SPDX does, and holds exceptions to a date.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import contextlib
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_licenses  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TODAY = "2026-11-25"


class TestDeniedFamilies(unittest.TestCase):
    def test_each_denied_family_is_recognised_in_its_common_spellings(self) -> None:
        cases = {
            "AGPL-3.0-only": ("AGPL",),
            "GNU Affero General Public License v3": ("AGPL",),
            "GPL-2.0": ("GPL",),
            "GPLv3+": ("GPL",),
            "GNU General Public License v3 (GPLv3)": ("GPL",),
            "GPL-2.0 WITH Classpath-exception-2.0": ("GPL",),
            "SSPL-1.0": ("SSPL",),
            "Server Side Public License": ("SSPL",),
            "BUSL-1.1": ("BUSL",),
            "Business Source License 1.1": ("BUSL",),
        }
        for text, expected in cases.items():
            self.assertEqual(check_licenses.denied_families(text), expected, text)

    def test_permissive_and_weak_copyleft_licences_pass(self) -> None:
        for text in (
            "MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "MPL-2.0", "LGPL-3.0-or-later", "LGPLv2+",
            "GNU Lesser General Public License v3 or later (LGPLv3+)", "GNU Library or Lesser General Public License (LGPL)",
            "Python Software Foundation License", "UNKNOWN", "",
        ):
            self.assertEqual(check_licenses.denied_families(text), (), text)

    def test_an_alternative_we_may_take_clears_an_or_expression(self) -> None:
        self.assertEqual(check_licenses.denied_families("(MIT OR GPL-3.0)"), ())
        self.assertEqual(check_licenses.denied_families("GPL-2.0 OR (Apache-2.0 AND BSD-3-Clause)"), ())
        self.assertEqual(check_licenses.denied_families("GPL-2.0 OR AGPL-3.0"), ("AGPL", "GPL"))

    def test_any_denied_part_fails_an_and_expression_or_an_unexplained_list(self) -> None:
        self.assertEqual(check_licenses.denied_families("MIT AND GPL-3.0"), ("GPL",))
        self.assertEqual(check_licenses.denied_families("(MIT OR Apache-2.0) AND SSPL-1.0"), ("SSPL",))
        self.assertEqual(check_licenses.denied_families("MIT License; GNU General Public License (GPL)"), ("GPL",))
        self.assertEqual(check_licenses.denied_families("MIT, BUSL-1.1"), ("BUSL",))


class TestRun(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name: str, data: object) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def run_check(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status = check_licenses.main([*args, "--today", TODAY])
        return status, out.getvalue()

    def npm(self, **packages: object) -> str:
        return f"node={self.write('npm.json', {key: {'licenses': value} for key, value in packages.items()})}"

    def pip(self, *packages: tuple[str, str]) -> str:
        return f"python={self.write('pip.json', [{'Name': n, 'Version': '1.0', 'License': l} for n, l in packages])}"

    def exceptions(self, *entries: dict) -> str:
        return str(self.write("exceptions.json", {"exceptions": list(entries)}))

    def entry(self, **changes: str) -> dict:
        return {
            "ecosystem": "pypi", "package": "Copy_Left", "license": "GPL-3.0", "reason": "build-time only, not shipped",
            "reviewed": "2026-11-01", "expires": "2027-05-01",
        } | changes

    def test_clean_listings_pass_and_are_summarised(self) -> None:
        status, out = self.run_check(
            "--npm", self.npm(**{"left-pad@1.3.0": "MIT", "@scope/pkg@2.0.0": ["MIT", "Apache-2.0"]}),
            "--pip", self.pip(("requests", "Apache-2.0"), ("mystery", "UNKNOWN")),
        )
        self.assertEqual(status, 0, out)
        self.assertIn("node: 2 packages", out)
        self.assertIn("python: mystery@1.0", out)  # listed for review, not judged
        self.assertIn("OK", out)

    def test_a_denied_licence_fails_and_names_the_package(self) -> None:
        status, out = self.run_check("--npm", self.npm(**{"@scope/copyleft@2.0.0": "AGPL-3.0", "fine@1.0.0": "MIT"}))
        self.assertEqual(status, 1)
        self.assertIn("node: @scope/copyleft@2.0.0  AGPL-3.0  [AGPL]", out)
        self.assertNotIn("fine@1.0.0  MIT", out)

    def test_a_reviewed_exception_covers_its_package_while_the_licence_is_the_one_reviewed(self) -> None:
        listing = self.pip(("copy-left", "GPL-3.0"))
        status, out = self.run_check("--pip", listing, "--exceptions", self.exceptions(self.entry()))
        self.assertEqual(status, 0, out)
        self.assertIn("covered by a reviewed exception (1)", out)

        status, out = self.run_check("--pip", listing, "--exceptions", self.exceptions(self.entry(license="GPL-2.0")))
        self.assertEqual(status, 1)
        self.assertIn("DENIED (1)", out)
        self.assertIn("matches no listed package", out)

    def test_an_expired_incomplete_or_unused_exception_fails_the_run(self) -> None:
        listing = self.pip(("copy-left", "GPL-3.0"))
        for entry, message in (
            (self.entry(expires="2026-11-24"), "expired on 2026-11-24"),
            (self.entry(reason=" "), "missing reason"),
            (self.entry(reviewed="soon"), "must be YYYY-MM-DD"),
            (self.entry(reviewed="2026-12-01"), "has not happened yet"),
            (self.entry(ecosystem="cargo"), "ecosystem must be one of"),
        ):
            status, out = self.run_check("--pip", listing, "--exceptions", self.exceptions(entry))
            self.assertEqual(status, 1, out)
            self.assertIn(message, out)
        status, out = self.run_check("--pip", self.pip(("requests", "Apache-2.0")), "--exceptions", self.exceptions(self.entry()))
        self.assertEqual(status, 1)
        self.assertIn("matches no listed package", out)

    def test_unusable_input_is_an_error_not_a_pass(self) -> None:
        empty = f"node={self.write('empty.json', {})}"
        wrong = f"python={self.write('wrong.json', {'not': 'a list'})}"
        missing = f"node={self.dir / 'absent.json'}"
        for args in (("--npm", empty), ("--pip", wrong), ("--npm", missing)):
            status, out = self.run_check(*args)
            self.assertEqual(status, 2, out)


class TestCheckedInFiles(unittest.TestCase):
    def test_the_exceptions_file_is_valid_today(self) -> None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        _usable, problems = check_licenses.load_exceptions(REPO / ".github" / "license-exceptions.json", today)
        self.assertEqual(problems, [])

    def test_the_workflow_pins_its_tools_and_scans_all_three_trees(self) -> None:
        workflow = (REPO / ".github" / "workflows" / "license-scan.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"license-checker-rseidelsohn@\d+\.\d+\.\d+")
        self.assertRegex(workflow, r"pip-licenses==\d+\.\d+\.\d+")
        for tree in ("backend/nodejs/apps", "backend/python", "frontend"):
            self.assertIn(tree, workflow)
        for line in workflow.splitlines():
            if "uses:" in line and "./" not in line:
                self.assertRegex(line, r"@[0-9a-f]{40}\b", line)


if __name__ == "__main__":
    unittest.main()
