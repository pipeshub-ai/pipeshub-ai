"""new_lint_findings.py lints main's versions without touching the checkout.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEW_FINDINGS = REPO / ".github" / "scripts" / "new_lint_findings.py"
A = "backend/python/app/a.py"
B = "backend/python/app/b.py"

# Stands in for ruff: prints no findings, and on the second call (the baseline) kills the check, as a cancelled job would.
FAKE_RUFF = f"""#!{sys.executable}
import os, signal, sys
calls = os.environ["FAKE_RUFF_CALLS"]
n = len(open(calls).read()) if os.path.exists(calls) else 0
open(calls, "a").write("x")
if n == 1 and os.environ.get("FAKE_RUFF_KILL"):
    os.kill(os.getppid(), signal.SIGKILL)
print("[]")
"""


class TestBaselineLeavesCheckoutAlone(unittest.TestCase):
    """The branch forked from main, then main moved: it changed a.py and added b.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / "backend" / "python" / "app").mkdir(parents=True)
        self._git("init", "-q", "-b", "main")
        self._commit({A: 'logger.info("✅ one")\n'})
        self._git("checkout", "-q", "-b", "pr")
        self._commit({A: 'logger.info("✅ one")\nlogger.info("❌ two")\n'})
        self._git("checkout", "-q", "main")
        self._commit({A: "x = 1\n", B: 'logger.info("✅ main only")\n'})
        self.main = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "-q", "pr")
        (self.repo / "untracked.txt").write_text("keep\n", encoding="utf-8")
        self.status = self._git("status", "--porcelain", "--untracked-files=all")
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        (bin_dir / "ruff").write_text(FAKE_RUFF, encoding="utf-8")
        (bin_dir / "ruff").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_RUFF_CALLS": str(Path(self._tmp.name) / "calls"), "TMPDIR": self._tmp.name}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, text=True,
                              check=True).stdout

    def _commit(self, files: dict[str, str]) -> None:
        for path, source in files.items():
            (self.repo / path).write_text(source, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "c")

    def _check(self, tool: str, *files: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(NEW_FINDINGS), tool, self.main, *files], cwd=self.repo,
                              env={**self.env, **env}, capture_output=True, text=True, check=False)

    def _assert_checkout_unchanged(self) -> None:
        self.assertEqual(self._git("status", "--porcelain", "--untracked-files=all"), self.status)
        self.assertEqual((self.repo / A).read_text(encoding="utf-8"), 'logger.info("✅ one")\nlogger.info("❌ two")\n')

    def test_killed_during_the_baseline_lint(self) -> None:
        result = self._check("ruff", A, FAKE_RUFF_KILL="1")
        self.assertEqual(result.returncode, -9, result.stdout + result.stderr)
        self._assert_checkout_unchanged()

    def test_a_file_main_added_after_the_fork(self) -> None:
        result = self._check("ruff", A, B)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._assert_checkout_unchanged()

    def test_new_findings_are_counted_against_the_base(self) -> None:
        result = self._check("log-emoji", A)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{A}: 2 new LOG-EMOJI (main has 0, this pull request has 2); lines 1, 2", result.stdout)
        self._assert_checkout_unchanged()


if __name__ == "__main__":
    unittest.main()
