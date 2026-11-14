"""Every graph test of permission-conformance.yml runs in exactly one shard.

The `graph` job splits two suites across `matrix.shard`, each shard with its own
stores. A shard names its pytest paths in `tests`. For each suite one shard
takes the whole folder minus what the others name (`--ignore`), so a new test
file is never left out. A file too slow for one shard runs alone in several,
each with a `k` expression; for every test exactly one of them is true. Each
shard pins its case count in `cases`, and a suite's pins add up to TOTALS.

Run: python3 -m unittest discover -s scripts -p 'test_permission_conformance_shards.py'
"""

import itertools
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/permission-conformance.yml"
PYTHON = ROOT / "backend/python"

# Cases of each suite under `-m integration` (the junit classname prefix). Adding tests raises this and the cases of the shard that runs them.
TOTALS = {"tests.integration.graph_permissions": 842, "tests.permissions": 313}


def _holds(expression: str, true: set[str]) -> bool:
    """A pytest -k expression with each name in `true` matched and every other name not."""
    words = re.findall(r"\(|\)|[^\s()]+", expression)
    code = " ".join(w if w in ("(", ")", "and", "or", "not") else str(w in true) for w in words)
    return bool(eval(code, {"__builtins__": {}}))  # noqa: S307 - only True/False/and/or/not/parentheses


def _partition(expressions: list[str]) -> bool:
    """Whether every combination of the names matching or not makes exactly one expression true."""
    names = sorted({w for e in expressions for w in re.findall(r"[^\s()]+", e)} - {"and", "or", "not"})
    for bits in itertools.product([False, True], repeat=len(names)):
        true = {n for n, b in zip(names, bits) if b}
        if sum(_holds(e, true) for e in expressions) != 1:
            return False
    return True


class PermissionConformanceShardsTest(unittest.TestCase):
    def setUp(self) -> None:
        matrix = yaml.safe_load(WORKFLOW.read_text())["jobs"]["graph"]["strategy"]["matrix"]
        self.names = matrix["shard"]
        self.shards = {entry["shard"]: entry for entry in matrix["include"]}
        self.args = {shard: entry["tests"].split() for shard, entry in self.shards.items()}

    def test_every_shard_has_its_suite_tests_and_pin(self) -> None:
        self.assertEqual(sorted(self.names), sorted(self.shards))
        for shard, entry in self.shards.items():
            self.assertIn(entry["suite"], TOTALS, shard)
            self.assertIsInstance(entry["cases"], int, shard)
            self.assertGreater(entry["cases"], 0, shard)
            self.assertTrue(self.args[shard], shard)

    def test_the_pins_add_up_to_each_suite(self) -> None:
        for suite, total in TOTALS.items():
            self.assertEqual(sum(e["cases"] for e in self.shards.values() if e["suite"] == suite), total, suite)

    def test_each_test_runs_in_exactly_one_shard(self) -> None:
        for suite in TOTALS:
            folder = suite.replace(".", "/")
            mine = {shard: self.args[shard] for shard, e in self.shards.items() if e["suite"] == suite}
            rest = [shard for shard, args in mine.items() if folder in args]
            self.assertEqual(len(rest), 1, f"{suite}: exactly one shard runs the folder minus the others' files")
            self.assertNotIn("k", self.shards[rest[0]], "-k would drop tests of new files from the rest shard")
            named: dict[str, list[int]] = {}
            for shard, args in mine.items():
                if shard == rest[0]:
                    continue
                for path in args:
                    self.assertTrue(path.startswith(f"{folder}/test_") and path.endswith(".py"), path)
                    self.assertTrue((PYTHON / path).is_file(), path)
                    named.setdefault(path, []).append(shard)
            for path, shards in named.items():
                ks = [self.shards[shard].get("k") for shard in shards]
                if len(shards) == 1:
                    self.assertIsNone(ks[0], f"{path}: a -k shard without the rest of its split drops tests")
                    continue
                # -k applies to the whole command, so a split file must be alone in each of its shards.
                for shard in shards:
                    self.assertEqual(self.args[shard], [path], f"shard {shard} splits {path} by -k and must run only it")
                self.assertTrue(all(ks), f"{path} runs in shards {shards}, but not split by -k")
                self.assertTrue(_partition(ks), f"{path}: the -k expressions {ks} do not hold each test exactly once")

            others = [a for a in self.args[rest[0]] if a != folder]
            self.assertTrue(all(a.startswith("--ignore=") for a in others), others)
            ignored = [a.removeprefix("--ignore=") for a in others]
            self.assertEqual(len(ignored), len(set(ignored)))
            # An ignore no other shard names would drop that file; a named file not ignored would run twice.
            self.assertEqual(set(ignored), set(named), suite)

    def test_each_allowed_skip_is_in_a_file_its_shard_runs(self) -> None:
        for shard, entry in self.shards.items():
            folder = entry["suite"].replace(".", "/")
            args = self.args[shard]
            for allow in entry.get("allow", "").split():
                path = f"{folder}/{allow.split('::')[0]}.py"
                self.assertTrue((PYTHON / path).is_file(), f"shard {shard}: {allow}")
                runs = path in args or (folder in args and f"--ignore={path}" not in args)
                self.assertTrue(runs, f"shard {shard} allows a skip in {path}, which it does not run")

    def test_the_partition_check(self) -> None:
        self.assertTrue(_partition(["[adversarial]", "not [adversarial]"]))
        self.assertTrue(_partition(["[x-y]", "not [x-y] and z", "not [x-y] and not z"]))
        self.assertFalse(_partition(["a", "b"]))                  # a test matching neither runs nowhere
        self.assertFalse(_partition(["a", "not a", "a and b"]))   # a test matching a and b runs twice


if __name__ == "__main__":
    unittest.main()
