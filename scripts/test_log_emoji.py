"""Emoji in log calls are found, and only new ones fail a pull request.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import log_emoji  # noqa: E402

CHECK = "✅"
CROSS = "❌"
WARN = "⚠️"
REPO = Path(__file__).resolve().parents[1]
NEW_FINDINGS = REPO / ".github" / "scripts" / "new_lint_findings.py"


def rows(source: str) -> list[int]:
    return log_emoji.findings(source)


class TestFindings(unittest.TestCase):
    def test_log_calls_with_emoji_are_found(self) -> None:
        for source in (
            f'logger.info("{CHECK} done")',
            f'self.logger.error(f"{CROSS} failed {{x}}")',
            f'logging.warning("{WARN} slow")',
            f'log.log(level, "{WARN} x")',
            f'logger.info("{CHECK} %s" % name)',
            f'logger.debug("a " + "{CHECK}")',
            f'logger.info(msg="{CHECK}")',
            'logger.info("\\u2139 info")',
            'logger.info("\\u203c")',
            'logger.exception("\\U0001F680 boom")',
        ):
            with self.subTest(source=source):
                self.assertEqual(rows(source), [1])

    def test_other_text_is_not_flagged(self) -> None:
        for source in (
            f'print("{CHECK}")',
            f'logger.info("ok")  # {CHECK}',
            f'logger.info("%s", s.split("{CHECK}"))',
            'logger.info("a → b, café")',
            f'x = "{CHECK}"',
            f'logger.info("count", extra={{"k": "{CHECK}"}})',
        ):
            with self.subTest(source=source):
                self.assertEqual(rows(source), [])

    def test_each_call_is_one_finding_at_its_line(self) -> None:
        source = f'a = 1\nlogger.info("{CHECK}")\nlogger.info(\n    "{CROSS} %s", x)\n'
        self.assertEqual(sorted(rows(source)), [2, 3])

    def test_a_file_that_does_not_parse_has_no_findings(self) -> None:
        self.assertEqual(rows(f'logger.info("{CHECK}"'), [])

    def test_prints_ruff_shaped_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text(f'logger.info("{CHECK}")\n', encoding="utf-8")
            out = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "log_emoji.py"), str(path)],
                capture_output=True, text=True, check=True,
            ).stdout
        self.assertEqual(json.loads(out), [{"filename": str(path), "code": "LOG-EMOJI", "location": {"row": 1}}])


class TestOnlyNewFindingsFail(unittest.TestCase):
    """new_lint_findings.py log-emoji against a throwaway git repository."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.file = self.repo / "backend" / "python" / "app" / "m.py"
        self.file.parent.mkdir(parents=True)
        self._git("init", "-q", "-b", "main")
        self._commit(f'logger.info("{CHECK} one")\n')
        self.base = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, text=True,
                              check=True).stdout

    def _commit(self, source: str) -> None:
        self.file.write_text(source, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "c")

    def _check(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(NEW_FINDINGS), "log-emoji", self.base, "backend/python/app/m.py"],
            cwd=self.repo, capture_output=True, text=True, check=False,
        )

    def test_a_new_emoji_log_call_fails(self) -> None:
        self._commit(f'logger.info("{CHECK} one")\nlogger.error("{CROSS} two")\n')
        result = self._check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backend/python/app/m.py", result.stdout)
        self.assertIn("1 new LOG-EMOJI", result.stdout)

    def test_editing_the_existing_line_passes(self) -> None:
        self._commit(f'x = 1\nlogger.info("{CHECK} one, reworded")\n')
        result = self._check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.file.read_text(encoding="utf-8"), f'x = 1\nlogger.info("{CHECK} one, reworded")\n')


if __name__ == "__main__":
    unittest.main()
