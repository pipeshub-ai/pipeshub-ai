"""The sign-off step in .github/workflows/dco.yml, run offline against fixtures.

The step's own script runs under bash with a stub `gh` that serves the pull
request and its commits as GitHub's API would, built from a real local git
history, and with UPSTREAM pointed at a local stand-in for pipeshub-ai main.

Stdlib only, like the rest of scripts/: it needs git, jq and bash on PATH.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[1]))
WORKFLOW = REPO / ".github" / "workflows" / "dco.yml"
OWNER_REPO = "pipeshub-ai/pipeshub-ai"
NUMBER = "7"

STUB_GH = r'''#!/usr/bin/env python3
"""Answers exactly the `gh api` calls the sign-off step makes, and refuses any other."""
import json, os, sys
world = json.load(open(os.environ["DCO_WORLD"]))
args = sys.argv[1:]
repo, base, head = world["repo"], world["base"], world["pr"]["head"]["sha"]
compare = f"repos/{repo}/compare/{base}...{head}"
if args == ["api", f"repos/{repo}/pulls/{world['number']}"]:
    print(json.dumps(world["pr"]))
elif args == ["api", "--paginate", f"{compare}?per_page=100", "--jq", ".commits[] | @json"]:
    for commit in world["listed"]:
        print(json.dumps(commit))
elif args == ["api", f"{compare}?per_page=1", "--jq", ".total_commits"]:
    print(world["total"])
else:
    sys.exit(f"stub gh: unexpected call {args}")
'''


def _step() -> tuple[str, dict[str, str]]:
    """The sign-off step's `run:` script and its `env:` block, read from dco.yml."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    run_at = next(i for i, line in enumerate(lines) if re.match(r"^\s+run: \|\s*$", line))
    run_indent = len(lines[run_at]) - len(lines[run_at].lstrip())
    body = []
    for line in lines[run_at + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        body.append(line)
    env_at = next(i for i, line in enumerate(lines) if re.match(r"^\s+env:\s*$", line))
    env = {}
    for line in lines[env_at + 1: run_at]:
        key, _, value = line.strip().partition(": ")
        env[key] = value
    return textwrap.dedent("\n".join(body)) + "\n", env


def _git(cwd: Path, *args: str, **env: str) -> str:
    full_env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **env}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=full_env, check=True, capture_output=True, text=True
    ).stdout.strip()


class _History:
    """A pipeshub-ai main, a base branch that has merged it, and a pull request branch on top."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.upstream = root / "upstream"
        self.work = root / "work"
        _git(root, "init", "-q", "-b", "main", str(self.upstream))
        _git(self.upstream, "config", "uploadpack.allowFilter", "true")
        self.upstream_commit = self._commit(self.upstream, "upstream work, never signed here", "dev@oss.example")
        _git(root, "clone", "-q", str(self.upstream), str(self.work))
        _git(self.work, "checkout", "-q", "--orphan", "base-main")
        self.base = self._commit(self.work, "base main", "ee@ee.example")
        _git(self.work, "checkout", "-q", "-b", "pr")
        _git(self.work, "-c", "user.name=m", "-c", "user.email=m@ee.example", "merge", "-q",
             "--allow-unrelated-histories", "--no-edit", "origin/main")
        self.merge = _git(self.work, "rev-parse", "HEAD")

    def _commit(self, repo: Path, message: str, email: str) -> str:
        _git(repo, "commit", "-q", "--allow-empty", "-m", message,
             GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL=email)
        return _git(repo, "rev-parse", "HEAD")

    def commit(self, message: str, email: str = "dev@ee.example") -> str:
        return self._commit(self.work, message, email)

    def api_commits(self, overrides: dict[str, dict] | None = None) -> list[dict]:
        """What GitHub's compare API lists for base...head, oldest first."""
        overrides = overrides or {}
        shas = _git(self.work, "rev-list", "--reverse", "HEAD", "--not", self.base).split()
        commits = []
        for sha in shas:
            parents = _git(self.work, "log", "-1", "--format=%P", sha).split()
            commit = {
                "sha": sha,
                "parents": [{"sha": p} for p in parents],
                "commit": {
                    "author": {"email": _git(self.work, "log", "-1", "--format=%ae", sha)},
                    "message": _git(self.work, "log", "-1", "--format=%B", sha),
                    "verification": {"verified": False},
                },
                "author": {"login": "dev"},
                "committer": {"login": "dev"},
            }
            for key, value in overrides.get(sha, {}).items():
                commit[key] = {**commit.get(key, {}), **value} if isinstance(value, dict) else value
            commits.append(commit)
        return commits


class TestDcoStep(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dco-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.history = _History(self.tmp)
        self.script, self.env = _step()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(STUB_GH)
        (bin_dir / "gh").chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    def run_step(self, commits: list[dict], *, opener: str = "dev", opener_type: str = "User",
                 total: int | None = None) -> subprocess.CompletedProcess:
        head = self.history.work
        world = {
            "repo": OWNER_REPO,
            "number": NUMBER,
            "base": self.history.base,
            "pr": {"head": {"sha": _git(head, "rev-parse", "HEAD")}, "user": {"login": opener, "type": opener_type}},
            "listed": commits,
            "total": len(commits) if total is None else total,
        }
        (self.tmp / "world.json").write_text(json.dumps(world))
        job = self.tmp / "job"
        shutil.rmtree(job, ignore_errors=True)
        job.mkdir()
        env = {
            "PATH": self.path,
            "HOME": str(self.tmp),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "DCO_WORLD": str(self.tmp / "world.json"),
            "NUMBER": NUMBER,
            "BASE": self.history.base,
            "REPO": OWNER_REPO,
            "GH_TOKEN": "unused",
            "UPSTREAM": f"file://{self.history.upstream}",
        }
        return subprocess.run(
            ["bash", "-c", self.script], cwd=job, env=env, capture_output=True, text=True, check=False
        )

    def flagged(self, result: subprocess.CompletedProcess) -> list[str]:
        return re.findall(r"^::error::([0-9a-f]{9}) ", result.stdout, re.MULTILINE)

    def test_the_step_reads_only_the_pr_number_and_base_from_the_event(self) -> None:
        self.assertIn("gh api", self.script)
        self.assertEqual(
            {k: v for k, v in self.env.items() if "github.event" in v},
            {"NUMBER": "${{ github.event.pull_request.number }}", "BASE": "${{ github.event.pull_request.base.sha }}"},
        )
        self.assertEqual(self.env["UPSTREAM"], "https://github.com/pipeshub-ai/pipeshub-ai.git")

    def test_signed_commits_pass(self) -> None:
        self.history.commit("feat: a\n\nSigned-off-by: Dev <dev@ee.example>")
        self.history.commit("fix: b\n\nSigned-off-by: Dev <DEV@EE.example>")
        self.history.commit("fix: c\n\nbody\n---\nSigned-off-by: Dev <dev@ee.example>")
        self.history.commit("fix: d\n\nCo-Authored-By: X <x@x.example>\nSigned-off-by: Dev\n  <dev@ee.example>")
        result = self.run_step(self.history.api_commits())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.flagged(result), [])

    def test_upstream_commits_and_merges_are_skipped_and_only_the_unsigned_are_flagged(self) -> None:
        unsigned = self.history.commit("feat: no trailer")
        other = self.history.commit("feat: someone else signed\n\nSigned-off-by: Other <other@ee.example>")
        not_a_trailer = self.history.commit("feat: inline\nSigned-off-by: Dev <dev@ee.example>")
        self.history.commit("feat: fine\n\nSigned-off-by: Dev <dev@ee.example>")
        commits = self.history.api_commits()
        listed = {c["sha"] for c in commits}
        self.assertIn(self.history.upstream_commit, listed, "the fixture must list an upstream commit")
        self.assertIn(self.history.merge, listed, "the fixture must list a merge commit")

        result = self.run_step(commits)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.flagged(result), [unsigned[:9], other[:9], not_a_trailer[:9]])
        self.assertIn("has no Signed-off-by trailer for <dev@ee.example>", result.stdout)

    def test_an_upstream_commit_counts_only_once_it_is_on_upstream_main(self) -> None:
        # The same unsigned commit, absent from upstream main, is flagged.
        _git(self.history.upstream, "reset", "-q", "--hard", "HEAD")
        _git(self.history.upstream, "commit", "-q", "--amend", "--allow-empty", "-m", "rewritten upstream",
             GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="dev@oss.example",
             GIT_COMMITTER_NAME="c", GIT_COMMITTER_EMAIL="dev@oss.example")
        result = self.run_step(self.history.api_commits())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.flagged(result), [self.history.upstream_commit[:9]])

    def test_bot_commits_pass_only_when_github_signed_them_for_the_opener(self) -> None:
        bot = self.history.commit("chore(deps): bump x\n\nSigned-off-by: dependabot[bot] <support@github.com>",
                                  email="49699333+dependabot[bot]@users.noreply.github.com")
        signed_by_github = {"author": {"login": "dependabot[bot]"}, "committer": {"login": "web-flow"},
                            "commit": {"verification": {"verified": True}}}
        unverified = {**signed_by_github, "commit": {"verification": {"verified": False}}}

        def run(overrides: dict, opener: str, opener_type: str) -> list[str]:
            commits = self.history.api_commits({bot: overrides})
            return self.flagged(self.run_step(commits, opener=opener, opener_type=opener_type))

        self.assertEqual(run(signed_by_github, "dependabot[bot]", "Bot"), [])
        self.assertEqual(run(signed_by_github, "dependabot[bot]", "User"), [bot[:9]])
        self.assertEqual(run(signed_by_github, "renovate[bot]", "Bot"), [bot[:9]])
        self.assertEqual(run(unverified, "dependabot[bot]", "Bot"), [bot[:9]])

    def test_a_listing_shorter_than_the_pr_fails_closed(self) -> None:
        self.history.commit("feat: a\n\nSigned-off-by: Dev <dev@ee.example>")
        commits = self.history.api_commits()
        result = self.run_step(commits[:-1], total=len(commits))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"GitHub listed {len(commits) - 1} of this PR's {len(commits)} commits", result.stdout)

    def test_an_unexpected_api_call_stops_the_step(self) -> None:
        self.script = self.script.replace("?per_page=1\"", "?per_page=2\"", 1)
        self.history.commit("feat: a\n\nSigned-off-by: Dev <dev@ee.example>")
        result = self.run_step(self.history.api_commits())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("unexpected call", result.stderr)


if __name__ == "__main__":
    unittest.main()
