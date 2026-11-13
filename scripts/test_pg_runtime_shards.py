"""Every PostgreSQL graph provider test runs in exactly one shard of pg-runtime.yml.

The `python` job splits tests/integration/graph_db/postgres across `matrix.shard`.
Each shard names its files in `graph_tests`. One shard takes the whole folder
minus what the others name (`--ignore`), so a new test file is never left out.
A file too slow for one shard runs in two, one with `graph_k: X` and the other
with `graph_k: not X`, which select complementary sets of its tests. Each shard
pins its case count in `graph_cases`, and the pins add up to TOTAL.

Run: python3 -m unittest discover -s scripts -p 'test_pg_runtime_shards.py'
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/pg-runtime.yml"
PYTHON = ROOT / "backend/python"
SUITE = "tests/integration/graph_db/postgres"

# Cases in the whole suite. Adding tests raises this and the graph_cases of the shard that runs them.
TOTAL = 1714


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
                self.assertIsNone(ks[0], f"{path}: a -k shard without its complement drops tests")
                continue
            # -k applies to the whole command, so a split file must be alone in both its shards.
            self.assertEqual(len(shards), 2, f"{path} is in shards {shards}")
            for shard in shards:
                self.assertEqual(self.args[shard], [path], f"shard {shard} splits {path} by -k and must run only it")
            self.assertTrue(all(ks), f"{path} runs in shards {shards}, but not split by -k")
            a, b = sorted(ks, key=len)
            self.assertTrue(b == f"not ({a})" or (a.isidentifier() and b == f"not {a}"),
                            f"{path}: -k {a!r} and {b!r} are not complements")
        ignored = [a.removeprefix("--ignore=") for a in self.args[rest[0]] if a != SUITE]
        self.assertTrue(all(a.startswith("--ignore=") for a in self.args[rest[0]] if a != SUITE))
        self.assertEqual(len(ignored), len(set(ignored)))
        # An ignore no other shard names would drop that file; a named file not ignored would run twice.
        self.assertEqual(set(ignored), set(named))


if __name__ == "__main__":
    unittest.main()
