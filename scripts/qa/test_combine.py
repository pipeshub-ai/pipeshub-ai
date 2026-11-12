"""qa combine: PRs stacked on a base, one commit each, conflict resolutions in commits of their own."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from qa import combine


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="qa-combine-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "qa@example.com")
        git(self.root, "config", "user.name", "QA")
        self.write("app/settings.py", "A = 1\nB = 2\nC = 3\n")
        self.write("app/other.py", "x = 1\n")
        self.write("app/logo.bin", "\0\1\2")
        self.base = self.commit("base")

    def write(self, rel: str, body: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def commit(self, msg: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", msg)
        return git(self.root, "rev-parse", "HEAD")

    def pr(self, number: int, files: dict[str, str | None], commits: int = 1) -> dict:
        git(self.root, "checkout", "-q", "-b", f"pr{number}", self.base)
        for i in range(commits):
            for rel, body in files.items():
                if body is None:
                    (self.root / rel).unlink()
                else:
                    self.write(rel, body + ("" if i == commits - 1 else f"# step {i}\n"))
            sha = self.commit(f"pr {number} commit {i}")
        git(self.root, "checkout", "-q", "main")
        return {"number": number, "sha": sha, "title": f"PR {number}"}

    def log(self) -> list[str]:
        return git(self.root, "log", "--format=%s", f"{self.base}..HEAD").splitlines()[::-1]


def no_resolver(root, files, ctx):
    raise AssertionError("no conflict expected")


class TestClean(Repo):
    def test_each_pr_is_one_commit_in_order_and_there_are_no_merges(self) -> None:
        a = self.pr(1, {"app/settings.py": "A = 10\nB = 2\nC = 3\n"}, commits=3)
        b = self.pr(2, {"app/other.py": "x = 2\n"})
        result = combine.build(self.root, "main", [a, b], no_resolver)
        self.assertEqual(self.log(), ["combine: #1 PR 1", "combine: #2 PR 2"])
        self.assertEqual(git(self.root, "rev-list", "--merges", f"{self.base}..HEAD"), "")
        self.assertEqual((self.root / "app/settings.py").read_text(), "A = 10\nB = 2\nC = 3\n")
        self.assertEqual(result["head"], git(self.root, "rev-parse", "HEAD"))
        self.assertEqual([p["files"] for p in result["prs"]], [["app/settings.py"], ["app/other.py"]])
        self.assertEqual(result["conflicts"], [])
        self.assertIn("Signed-off-by:", git(self.root, "log", "-1", "--format=%B"))

    def test_a_pr_already_contained_adds_no_commit(self) -> None:
        a = self.pr(1, {"app/other.py": "x = 2\n"})
        result = combine.build(self.root, "main", [a, dict(a, number=2)], no_resolver)
        self.assertEqual(self.log(), ["combine: #1 PR 1"])
        self.assertIsNone(result["prs"][1]["commit"])


class TestConflicts(Repo):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.pr(1, {"app/settings.py": "A = 1\nB = 20\nC = 3\n"})
        self.b = self.pr(2, {"app/settings.py": "A = 1\nB = 200\nC = 3\nD = 4\n"})

    def test_the_resolution_is_its_own_commit_after_the_pr_commit(self) -> None:
        seen = {}

        def resolve(root, files, ctx):
            seen.update(files=files, against=ctx["against"], marked=(root / files[0]).read_text())
            (root / files[0]).write_text("A = 1\nB = 220\nC = 3\nD = 4\n")
        result = combine.build(self.root, "main", [self.a, self.b], resolve)
        self.assertEqual(seen["files"], ["app/settings.py"])
        self.assertEqual(seen["against"], [1])
        self.assertIn("<<<<<<<", seen["marked"])
        self.assertEqual(self.log(), ["combine: #1 PR 1", "combine: #2 PR 2", "combine: resolve #2 against #1 in app/settings.py"])
        # The PR's own commit keeps the combined side; the resolution commit holds only what the resolver chose.
        self.assertEqual(git(self.root, "show", "HEAD~1:app/settings.py"), "A = 1\nB = 20\nC = 3")
        self.assertEqual((self.root / "app/settings.py").read_text(), "A = 1\nB = 220\nC = 3\nD = 4\n")
        self.assertEqual(result["conflicts"][0]["files"], ["app/settings.py"])
        self.assertIn("+B = 220", result["conflicts"][0]["diff"])
        self.assertEqual(git(self.root, "rev-list", "--merges", f"{self.base}..HEAD"), "")

    def test_markers_left_by_the_resolver_stop_the_build(self) -> None:
        with self.assertRaisesRegex(combine.CombineError, "conflict markers left in app/settings.py"):
            combine.build(self.root, "main", [self.a, self.b], lambda root, files, ctx: None)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_a_resolver_that_touches_another_file_stops_the_build(self) -> None:
        def resolve(root, files, ctx):
            (root / files[0]).write_text("A = 1\nB = 220\nC = 3\n")
            (root / "app/other.py").write_text("x = 99\n")
            (root / "app/new.py").write_text("y = 1\n")
        with self.assertRaisesRegex(combine.CombineError, "the resolver also changed app/new.py, app/other.py"):
            combine.build(self.root, "main", [self.a, self.b], resolve)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_a_resolver_that_gives_up_stops_the_build_with_its_reason(self) -> None:
        with self.assertRaisesRegex(combine.CombineError, "not resolved: the claude CLI is not installed") as caught:
            combine.build(self.root, "main", [self.a, self.b], lambda root, files, ctx: "the claude CLI is not installed")
        self.assertEqual([p["number"] for p in caught.exception.result["prs"]], [1, 2])


class TestStops(Repo):
    def test_delete_against_change_stops(self) -> None:
        a = self.pr(1, {"app/other.py": None})
        b = self.pr(2, {"app/other.py": "x = 3\n"})
        with self.assertRaisesRegex(combine.CombineError, "deleted on one side and changed on the other"):
            combine.build(self.root, "main", [a, b], no_resolver)

    def test_binary_conflict_stops(self) -> None:
        a = self.pr(1, {"app/logo.bin": "\0\3"})
        b = self.pr(2, {"app/logo.bin": "\0\4"})
        with self.assertRaisesRegex(combine.CombineError, "binary conflict in app/logo.bin"):
            combine.build(self.root, "main", [a, b], no_resolver)

    def test_a_missing_head_stops(self) -> None:
        with self.assertRaisesRegex(combine.CombineError, "is not in this checkout"):
            combine.build(self.root, "main", [{"number": 5, "sha": "f" * 40, "title": "x"}], no_resolver)

    def test_too_many_prs_or_a_dirty_checkout_stop(self) -> None:
        with self.assertRaises(combine.CombineError):
            combine.build(self.root, "main", [], no_resolver)
        self.write("app/other.py", "dirty\n")
        with self.assertRaisesRegex(combine.CombineError, "uncommitted changes"):
            combine.build(self.root, "main", [{"number": 1, "sha": self.base}], no_resolver)


if __name__ == "__main__":
    unittest.main()
