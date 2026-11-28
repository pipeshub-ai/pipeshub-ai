"""scripts/sim_shard_env.py: the fake model and strict settings reach simulator shards, and nothing else."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sim_shard_env import FAKE_MODEL_ENV, REPO, env_lines

TIERS = {"schema": 1, "sim_shards": ["connectors-6"], "suites": {"zammad_sim": {"pr": "all", "nightly": "all"}}}


class SimShardEnv(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tiers = Path(folder.name) / "sim_tiers.json"
        self.tiers.write_text(json.dumps(TIERS))

    def test_a_sim_shard_gets_the_fake_model_and_strict_settings(self) -> None:
        lines = env_lines("connectors-6", "", self.tiers)
        self.assertEqual(lines, [f"{key}={value}" for key, value in FAKE_MODEL_ENV])
        self.assertIn("TEST_AI_MODEL_PROVIDER=ollama", lines)
        self.assertIn("PIPESHUB_REQUIRE_CONNECTOR_SECRETS=1", lines)
        self.assertIn("RUN_PLAYWRIGHT=false", lines)
        self.assertIn("SIM_JOB=1", lines)

    def test_a_dispatch_of_a_sim_suite_gets_it_too(self) -> None:
        self.assertTrue(env_lines("dispatch", "zammad_sim", self.tiers))
        self.assertTrue(env_lines("dispatch", " zammad_sim\n", self.tiers))

    def test_live_shards_and_live_dispatches_get_nothing(self) -> None:
        for shard, connectors in (
            ("connectors-2", ""), ("core", ""), ("demo", ""), ("ai_agents", ""),
            ("dispatch", "zammad"), ("dispatch", "integration"), ("dispatch", ""), ("connectors-6x", ""),
        ):
            with self.subTest(shard=shard, connectors=connectors):
                self.assertEqual(env_lines(shard, connectors, self.tiers), [])

    def test_a_broken_tiers_file_fails_the_step(self) -> None:
        self.tiers.write_text("{")
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "sim_shard_env.py"), "--shard", "connectors-6", "--tiers", str(self.tiers)],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_the_committed_file_is_read(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "sim_shard_env.py"), "--shard", "connectors-1"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual((result.returncode, result.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
