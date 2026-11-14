"""Every PostgreSQL graph provider test runs in exactly one shard of pg-runtime.yml.

The `python` job splits tests/integration/graph_db/postgres across `matrix.shard`.
Each shard names its files in `graph_tests`. One shard takes the whole folder
minus what the others name (`--ignore`), so a new test file is never left out.
A file too slow for one shard runs alone in several, each with a `graph_k`
expression; for every test exactly one of them is true. Each shard pins its
case count in `graph_cases`, and the pins add up to TOTAL.

Run: python3 -m unittest discover -s scripts -p 'test_pg_runtime_shards.py'
"""

import itertools
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/pg-runtime.yml"
PYTHON = ROOT / "backend/python"
SUITE = "tests/integration/graph_db/postgres"

# Cases in the whole suite. Adding tests raises this and the graph_cases of the shard that runs them.
TOTAL = 1739


def _holds(expression: str, true: set[str]) -> bool:
    """A pytest -k expression with each name in `true` matched and every other name not."""
    words = re.findall(r"\(|\)|[^\s()]+", expression)
    names = [w for w in words if w not in ("(", ")", "and", "or", "not")]
    code = " ".join(w if w in ("(", ")", "and", "or", "not") else str(w in true) for w in words)
    return bool(eval(code, {"__builtins__": {}})) if names else False  # noqa: S307 - only True/False/and/or/not/parentheses


def _partition(expressions: list[str]) -> bool:
    """Whether every combination of the names matching or not makes exactly one expression true."""
    names = sorted({w for e in expressions for w in re.findall(r"[^\s()]+", e)} - {"and", "or", "not"})
    for bits in itertools.product([False, True], repeat=len(names)):
        true = {n for n, b in zip(names, bits) if b}
        if sum(_holds(e, true) for e in expressions) != 1:
            return False
    return True


class PgRuntimeShardsTest(unittest.TestCase):
    def setUp(self) -> None:
        matrix = yaml.safe_load(WORKFLOW.read_text())["jobs"]["python"]["strategy"]["matrix"]
        self.names = matrix["shard"]
        self.shards = {entry["shard"]: entry for entry in matrix["include"]}
        self.args = {shard: entry["graph_tests"].split() for shard, entry in self.shards.items()}

    def test_every_shard_has_its_tests_and_pin(self) -> None:
        self.assertEqual(sorted(self.names), sorted(self.shards))
        for shard, entry in self.shards.items():
            self.assertIsInstance(entry["graph_cases"], int, shard)
            self.assertGreater(entry["graph_cases"], 0, shard)
            self.assertTrue(self.args[shard], shard)

    def test_the_pins_add_up_to_the_suite(self) -> None:
        self.assertEqual(sum(entry["graph_cases"] for entry in self.shards.values()), TOTAL)

    def test_each_test_runs_in_exactly_one_shard(self) -> None:
        rest = [shard for shard, args in self.args.items() if SUITE in args]
        self.assertEqual(len(rest), 1, "exactly one shard runs the folder minus the others' files")
        self.assertNotIn("graph_k", self.shards[rest[0]], "-k would drop tests of new files from the rest shard")
        named: dict[str, list[int]] = {}
        for shard, args in self.args.items():
            if shard == rest[0]:
                continue
            for path in args:
                self.assertTrue(path.startswith(f"{SUITE}/test_") and path.endswith(".py"), path)
                self.assertTrue((PYTHON / path).is_file(), path)
                named.setdefault(path, []).append(shard)
        for path, shards in named.items():
            ks = [self.shards[shard].get("graph_k") for shard in shards]
            if len(shards) == 1:
                self.assertIsNone(ks[0], f"{path}: a -k shard without the rest of its split drops tests")
                continue
            # -k applies to the whole command, so a split file must be alone in each of its shards.
            for shard in shards:
                self.assertEqual(self.args[shard], [path], f"shard {shard} splits {path} by -k and must run only it")
            self.assertTrue(all(ks), f"{path} runs in shards {shards}, but not split by -k")
            self.assertTrue(_partition(ks), f"{path}: the -k expressions {ks} do not hold each test exactly once")

        ignored = [a.removeprefix("--ignore=") for a in self.args[rest[0]] if a != SUITE]
        self.assertTrue(all(a.startswith("--ignore=") for a in self.args[rest[0]] if a != SUITE))
        self.assertEqual(len(ignored), len(set(ignored)))
        # An ignore no other shard names would drop that file; a named file not ignored would run twice.
        self.assertEqual(set(ignored), set(named))

    def test_the_partition_check(self) -> None:
        self.assertTrue(_partition(["a", "not a"]))
        self.assertTrue(_partition(["[x-y]", "not [x-y] and z", "not [x-y] and not z"]))
        self.assertFalse(_partition(["a", "b"]))                  # a test matching neither runs nowhere
        self.assertFalse(_partition(["a", "not a", "a and b"]))   # a test matching a and b runs twice
        self.assertFalse(_partition(["not a or b", "a"]))

if __name__ == "__main__":
    unittest.main()
