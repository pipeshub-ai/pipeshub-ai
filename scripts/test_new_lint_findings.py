"""new_lint_findings.py lints main's versions without touching the checkout.

Run: TS_MODULE=<backend/nodejs/apps/node_modules/typescript> python3 -m unittest discover -s scripts -p 'test_*.py'
The relative TS_MODULE test needs that package (npm ci in backend/nodejs/apps sets it up); it fails without it, never skips.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEW_FINDINGS = REPO / ".github" / "scripts" / "new_lint_findings.py"
TS_MODULE = os.path.abspath(os.environ.get("TS_MODULE") or REPO / "backend" / "nodejs" / "apps" / "node_modules" / "typescript")
A = "backend/python/app/a.py"
B = "backend/python/app/b.py"
CONFIG = "backend/python/pyproject.toml"
TS = "backend/nodejs/apps/src/m.ts"
PKG = "backend/python/pkg"

# A caller such as a git hook may export these; the fixture must not use the caller's repository, index or config.
GIT_CALLER_VARS = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                   "GIT_COMMON_DIR", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
BASE_ENV = {**{k: v for k, v in os.environ.items() if k not in GIT_CALLER_VARS},
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# Stands in for ruff: reports every file when the config beside it says "strict", and on the second call (the
# baseline) can kill the check, as a cancelled job would.
FAKE_RUFF = f"""#!{sys.executable}
import json, os, signal, sys
calls = os.environ["FAKE_RUFF_CALLS"]
n = len(open(calls).read()) if os.path.exists(calls) else 0
open(calls, "a").write("x")
if n == 1 and os.environ.get("FAKE_RUFF_KILL"):
    os.kill(os.getppid(), signal.SIGKILL)
strict = os.path.isfile("pyproject.toml") and "strict" in open("pyproject.toml").read()
files = sys.argv[4:] if strict else []
print(json.dumps([{{"filename": os.path.abspath(f), "code": "STRICT", "location": {{"row": 1}}}} for f in files]))
"""


class TestBaselineLeavesCheckoutAlone(unittest.TestCase):
    """The branch forked from main, then main moved: it changed a.py and added b.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / "backend" / "python" / "app").mkdir(parents=True)
        (self.repo / "backend" / "nodejs" / "apps" / "src").mkdir(parents=True)
        self._git("init", "-q", "-b", "main")
        (self.repo / PKG).mkdir()
        self._commit({A: 'logger.info("✅ one")\n', CONFIG: "", TS: "export const a = 1;\n", f"{PKG}/m.py": "x = 1\n"})
        self._git("checkout", "-q", "-b", "pr")
        # The pull request turns the package into a link to a directory outside the repository.
        self.outside = Path(self._tmp.name) / "outside"
        self.outside.mkdir()
        (self.outside / "m.py").write_text("outside\n", encoding="utf-8")
        shutil.rmtree(self.repo / PKG)
        (self.repo / PKG).symlink_to(self.outside)
        self._commit({A: 'logger.info("✅ one")\nlogger.info("❌ two")\n', TS: "export const a = 2;\n"})
        self._git("checkout", "-q", "main")
        self._commit({A: 'x = 1\nlogger.info("✅ one")\n', B: 'logger.info("✅ main only")\n'})
        self.main = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "-q", "pr")
        (self.repo / "untracked.txt").write_text("keep\n", encoding="utf-8")
        (self.repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        self._git("add", "staged.txt")
        self.hook_marker = Path(self._tmp.name) / "hook-ran"
        hook = self.repo / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch '{self.hook_marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
        self._git("checkout", "-q", "pr")
        self.assertTrue(self.hook_marker.exists(), "the fixture's post-checkout hook does not run")
        self.hook_marker.unlink()
        self.status = self._git("status", "--porcelain", "--untracked-files=all")
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        (bin_dir / "ruff").write_text(FAKE_RUFF, encoding="utf-8")
        (bin_dir / "ruff").chmod(0o755)
        # git commit exports GIT_INDEX_FILE to its hooks, which may run this check; the staged file must survive.
        self.env = {**BASE_ENV, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_RUFF_CALLS": str(Path(self._tmp.name) / "calls"), "TMPDIR": self._tmp.name,
                    "GIT_INDEX_FILE": str(self.repo / ".git" / "index")}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, env=BASE_ENV, capture_output=True, text=True,
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
        self.assertFalse(self.hook_marker.exists(), "a repository hook ran on the baseline worktree")
        self.assertEqual((self.outside / "m.py").read_text(encoding="utf-8"), "outside\n")

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
        self.assertIn(f"{A}: 1 new LOG-EMOJI (main has 1, this pull request has 2); lines 1, 2", result.stdout)
        self._assert_checkout_unchanged()

    def test_an_uncommitted_config_applies_to_both_passes(self) -> None:
        (self.repo / CONFIG).write_text("strict\n", encoding="utf-8")
        self.status = self._git("status", "--porcelain", "--untracked-files=all")
        result = self._check("ruff", A)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No new ruff findings.", result.stdout)
        self._assert_checkout_unchanged()

    def test_a_path_under_a_linked_directory_is_not_written_through(self) -> None:
        result = self._check("ruff", f"{PKG}/m.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._assert_checkout_unchanged()

    def test_a_file_and_a_directory_swapped_in_the_checkout(self) -> None:
        (self.repo / CONFIG).unlink()
        (self.repo / CONFIG).mkdir()
        (self.repo / CONFIG / "x").write_text("x\n", encoding="utf-8")
        shutil.rmtree(self.repo / "backend" / "nodejs" / "apps" / "src")
        (self.repo / "backend" / "nodejs" / "apps" / "src").write_text("now a file\n", encoding="utf-8")
        self.status = self._git("status", "--porcelain", "--untracked-files=all")
        result = self._check("ruff", A)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._assert_checkout_unchanged()

    def test_an_untracked_file_name_that_is_not_utf8(self) -> None:
        (self.repo / os.fsdecode(b"bad\xff.txt")).write_text("x\n", encoding="utf-8")
        self.status = self._git("status", "--porcelain", "--untracked-files=all")
        result = self._check("ruff", A)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._assert_checkout_unchanged()

    def test_a_relative_ts_module(self) -> None:
        result = self._check("log-pii", TS, TS_MODULE=os.path.relpath(TS_MODULE, self.repo))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._assert_checkout_unchanged()


if __name__ == "__main__":
    unittest.main()
