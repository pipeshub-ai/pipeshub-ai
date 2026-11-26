"""deployment/e2e/rotate_logs.sh bounds the e2e stack's process logs (FU2: node.log filled the host disk)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "deployment/e2e/rotate_logs.sh"
MB = 1024 * 1024


def rotate(logs: Path) -> None:
    env = {**os.environ, "E2E_LOG_MAX_MB": "1", "E2E_LOG_KEEP": "2"}
    subprocess.run(["bash", str(SCRIPT), str(logs), "--once"], check=True, env=env, timeout=30)


class RotateLogsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_file_over_the_bound_keeps_its_newest_copies_and_its_writer(self) -> None:
        node = self.logs / "node.log"
        with open(node, "a") as writer:
            for generation in (b"a", b"b", b"c"):
                writer.write(generation.decode() * (MB + 1))
                writer.flush()
                rotate(self.logs)
            writer.write("after\n")
        self.assertEqual(node.read_text(), "after\n")
        self.assertEqual((self.logs / "node.log.1").read_bytes()[:1], b"c")
        self.assertEqual((self.logs / "node.log.2").read_bytes()[:1], b"b")
        self.assertFalse((self.logs / "node.log.3").exists())

    def test_a_file_under_the_bound_is_left_alone(self) -> None:
        query = self.logs / "query.log"
        query.write_text("small\n")
        rotate(self.logs)
        self.assertEqual(query.read_text(), "small\n")
        self.assertFalse((self.logs / "query.log.1").exists())


if __name__ == "__main__":
    unittest.main()
