"""Every real-graph test file runs in exactly one shard, and the workflow runs every shard on both graph stores.

Run: python3 -m unittest discover -s scripts -p 'test_graph_shards.py'
"""

import io
import sys
import unittest
import unittest.mock
from contextlib import redirect_stderr
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import graph_shards as gs

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/backend-matrix.yml"
ALL_SHARDS = range(1, gs.SHARD_COUNT + 1)


class GraphShardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.files = gs.discovered()

    def test_discovery_finds_the_real_graph_tests(self) -> None:
        self.assertGreater(len(self.files), 100)
        self.assertIn("tests/integration/graph_db/test_partial_write_failures_real_backends.py", self.files)

    def test_every_discovered_file_runs_in_exactly_one_shard(self) -> None:
        runs = [f for shard in ALL_SHARDS for f in gs.shard_files(shard, self.files)]
        self.assertEqual(sorted(runs), sorted(self.files))
        for shard in ALL_SHARDS:
            self.assertTrue(gs.shard_files(shard, self.files), f"shard {shard} would run no file")

    def test_a_new_file_runs_in_the_last_shard(self) -> None:
        new = "tests/integration/graph_db/test_not_yet_written_real_backends.py"
        self.assertIn(new, gs.shard_files(gs.SHARD_COUNT, [*self.files, new]))

    def test_every_named_path_is_discovered(self) -> None:
        self.assertEqual(sorted(gs.NAMED), list(range(1, gs.SHARD_COUNT)), "the last shard takes the rest and names nothing")
        named = [p for paths in gs.NAMED.values() for p in paths]
        self.assertEqual(len(named), len(set(named)), "a path named twice")
        for path in named:
            # a renamed or deleted file would leave its shard lighter and the last one heavier
            self.assertTrue(any(gs.under(f, path) for f in self.files), f"{path} names no discovered test file")

    def test_the_workflow_runs_every_shard_on_both_stores(self) -> None:
        include = yaml.safe_load(WORKFLOW.read_text())["jobs"]["backend"]["strategy"]["matrix"]["include"]
        graph = [entry for entry in include if entry["suite"] == "graph"]
        expected = sorted(f"{k}/{gs.SHARD_COUNT}" for k in ALL_SHARDS)
        for backend in ("neo4j", "arangodb"):
            shards = sorted(str(e.get("shard")) for e in graph if e["backend"] == backend)
            self.assertEqual(shards, expected, backend)
        self.assertEqual(len(graph), 2 * gs.SHARD_COUNT)

    def test_the_command_line(self) -> None:
        with redirect_stderr(io.StringIO()):
            for bad in ([], ["1"], [f"0/{gs.SHARD_COUNT}"], [f"1/{gs.SHARD_COUNT + 1}"], [f"{gs.SHARD_COUNT + 1}/{gs.SHARD_COUNT}"]):
                self.assertEqual(gs.main(bad), 2, bad)
            # pytest given no paths would run every test it finds
            with unittest.mock.patch.object(gs, "discovered", return_value=[]):
                self.assertEqual(gs.main([f"1/{gs.SHARD_COUNT}"]), 1)


if __name__ == "__main__":
    unittest.main()
