"""Every Python unit test runs in exactly one shard of unit-test.yml.

Stdlib only: main.yml runs `python3 -m unittest discover -s scripts` without
installing anything.
"""

import re
import tempfile
import unittest
from pathlib import Path

import unit_shards

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/unit-test.yml"


def _collected_by(args: list[str], test_file: str) -> bool:
    """Whether pytest, given `args`, collects `test_file`.

    An --ignore hides everything under it, a path named on the command line
    included: pytest walks down to that path and stops at the ignored directory.
    """
    ignores = [a.removeprefix("--ignore=") for a in args if a.startswith("--ignore=")]
    paths = [a for a in args if not a.startswith("--")]
    if any(unit_shards._contains(i, test_file) for i in ignores):
        return False
    return any(unit_shards._contains(p, test_file) for p in paths)


class UnitShardsTest(unittest.TestCase):
    def test_every_unit_test_runs_in_exactly_one_shard(self):
        files = unit_shards.test_files()
        self.assertGreater(len(files), 1000, "tests/unit looks empty; wrong root?")
        args = {s: unit_shards.pytest_args(s) for s in unit_shards.SHARDS}
        owners = unit_shards.owned_paths()
        for test_file in files:
            runners = [s for s, a in args.items() if _collected_by(a, test_file)]
            expected = owners[unit_shards.nearest_owner(test_file, owners)]
            self.assertEqual(runners, [expected], test_file)

    def test_shard_owns_matches_the_files_a_shard_collects(self):
        golden = "tests/unit/services/vector_db/test_bm25_golden.py"
        self.assertTrue((unit_shards.PYTHON_ROOT / golden).exists())
        owning = [s for s in unit_shards.SHARDS if unit_shards.shard_owns(s, golden)]
        self.assertEqual(owning, ["services"])
        for shard in unit_shards.SHARDS:
            self.assertEqual(
                unit_shards.shard_owns(shard, golden),
                _collected_by(unit_shards.pytest_args(shard), golden),
                shard,
            )

    def test_every_owned_path_exists(self):
        root = unit_shards.PYTHON_ROOT
        for shard, entries in unit_shards.SHARDS.items():
            for entry in entries:
                expanded = unit_shards._expand(entry, root)
                self.assertTrue(expanded, f"{shard}: {entry} matches nothing")
                for path in expanded:
                    self.assertTrue((root / path).exists(), f"{shard}: {path} does not exist")

    def test_every_shard_runs_some_tests(self):
        files = unit_shards.test_files()
        for shard in unit_shards.SHARDS:
            args = unit_shards.pytest_args(shard)
            self.assertTrue(any(_collected_by(args, f) for f in files), f"{shard} runs nothing")

    def test_no_shard_names_a_path_its_own_ignores_hide(self):
        for shard in unit_shards.SHARDS:
            args = unit_shards.pytest_args(shard)
            ignores = [a.removeprefix("--ignore=") for a in args if a.startswith("--ignore=")]
            for path in (a for a in args if not a.startswith("--")):
                hidden = [i for i in ignores if unit_shards._contains(i, path)]
                self.assertEqual(hidden, [], f"{shard}: {path} is under its own --ignore")

    def test_core_owns_the_unit_root(self):
        # Anything no other shard claims must still run somewhere.
        self.assertIn(unit_shards.UNIT_ROOT, unit_shards.SHARDS["core"])

    def test_workflow_matrix_lists_every_shard(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r"^\s+shard:\s*\[([^\]]*)\]", text, re.M)
        self.assertIsNotNone(match, "no `shard: [...]` matrix in unit-test.yml")
        listed = [s.strip().strip("'\"") for s in match.group(1).split(",") if s.strip()]
        self.assertEqual(listed, list(unit_shards.SHARDS))

    def test_the_shards_are_balanced(self):
        totals = unit_shards.shard_minutes(unit_shards.load_durations())
        self.assertEqual(unit_shards.balance_problems(totals), [])

    def test_the_measured_minutes_name_real_test_files(self):
        # A renamed or deleted file's minutes would no longer count anywhere.
        stale = set(unit_shards.load_durations()) - set(unit_shards.test_files())
        self.assertLess(len(stale), 50, f"refresh unit_shard_durations.json: {sorted(stale)[:10]}")

    def test_a_lopsided_split_is_reported(self):
        totals = dict.fromkeys(unit_shards.SHARDS, 5.0)
        totals["core"] = 20.0
        problems = unit_shards.balance_problems(totals)
        self.assertEqual(len(problems), 1)
        self.assertIn("core", problems[0])

    def test_a_junit_classname_maps_to_its_file(self):
        files = {"tests/unit/a/test_x.py"}
        self.assertEqual(unit_shards._file_of("tests.unit.a.test_x.TestY.TestZ", files), "tests/unit/a/test_x.py")
        self.assertEqual(unit_shards._file_of("tests.unit.a.test_x", files), "tests/unit/a/test_x.py")
        self.assertIsNone(unit_shards._file_of("tests.unit.b.test_x", files))

    def test_minutes_are_summed_per_file_from_junit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests/unit/a").mkdir(parents=True)
            (root / "tests/unit/a/test_x.py").touch()
            junit = root / "junit.xml"
            junit.write_text(
                '<testsuites><testsuite>'
                '<testcase classname="tests.unit.a.test_x.TestY" name="t1" time="30"/>'
                '<testcase classname="tests.unit.a.test_x" name="t2" time="60"/>'
                '<testcase classname="tests.unit.gone.test_z" name="t3" time="5"/>'
                '</testsuite></testsuites>',
                encoding="utf-8",
            )
            self.assertEqual(unit_shards.file_minutes([junit], root), {"tests/unit/a/test_x.py": 1.5})

    def test_a_path_inside_another_shards_directory_is_carved_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in ("tests/unit/a/test_x.py", "tests/unit/a/b/test_y.py", "tests/unit/test_z.py"):
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).touch()
            original = unit_shards.SHARDS
            unit_shards.SHARDS = {
                "outer": ("tests/unit/a",),
                "inner": ("tests/unit/a/b",),
                "core": ("tests/unit",),
            }
            try:
                self.assertEqual(
                    unit_shards.pytest_args("outer", root),
                    ["tests/unit/a", "--ignore=tests/unit/a/b"],
                )
                self.assertEqual(unit_shards.pytest_args("inner", root), ["tests/unit/a/b"])
                self.assertEqual(
                    unit_shards.pytest_args("core", root),
                    ["tests/unit", "--ignore=tests/unit/a"],
                )
            finally:
                unit_shards.SHARDS = original

    def test_a_path_of_ours_inside_another_shards_directory_is_still_reached(self):
        # The connectors-c shape: an ignore of the middle directory would hide it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in ("tests/unit/a/test_x.py", "tests/unit/a/b/test_y.py", "tests/unit/a/b/c/test_z.py"):
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).touch()
            original = unit_shards.SHARDS
            unit_shards.SHARDS = {
                "x": ("tests/unit/a", "tests/unit/a/b/c"),
                "y": ("tests/unit/a/b",),
                "core": ("tests/unit",),
            }
            try:
                self.assertEqual(
                    unit_shards.pytest_args("x", root),
                    ["tests/unit/a/b/c", "tests/unit/a/test_x.py"],
                )
                self.assertEqual(
                    unit_shards.pytest_args("y", root),
                    ["tests/unit/a/b", "--ignore=tests/unit/a/b/c"],
                )
                self.assertEqual(
                    unit_shards.pytest_args("core", root),
                    ["tests/unit", "--ignore=tests/unit/a"],
                )
            finally:
                unit_shards.SHARDS = original


if __name__ == "__main__":
    unittest.main()
