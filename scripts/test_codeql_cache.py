"""codeql.yml keeps a push to main from saving CodeQL caches nothing reads.

Every push used to save a 1.7 GB overlay-base database, which filled the
repository's 10 GB Actions cache and evicted every other cache. Only the
schedule (and workflow_dispatch) save one; pull requests restore it.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CODEQL = ROOT / ".github" / "workflows" / "codeql.yml"
PUSH_ONLY = "${{{{ github.event_name == 'push' && '{}' || '' }}}}"


def _init_step() -> dict:
    steps = yaml.safe_load(CODEQL.read_text())["jobs"]["analyze"]["steps"]
    (step,) = [s for s in steps if str(s.get("uses", "")).startswith("github/codeql-action/init@")]
    return step


class TestAPushSavesNoCodeqlCache(unittest.TestCase):
    def test_a_push_turns_overlay_base_off_and_nothing_else_does(self) -> None:
        self.assertEqual(_init_step()["env"]["CODEQL_OVERLAY_DATABASE_MODE"], PUSH_ONLY.format("none"))

    def test_a_push_turns_trap_caching_off_and_nothing_else_does(self) -> None:
        self.assertEqual(_init_step()["with"]["trap-caching"], PUSH_ONLY.format("false"))

    def test_the_schedule_and_dispatch_triggers_remain(self) -> None:
        workflow = yaml.safe_load(CODEQL.read_text())
        on = workflow.get(True) or workflow["on"]
        self.assertTrue(on["schedule"])
        self.assertIn("workflow_dispatch", on)


if __name__ == "__main__":
    unittest.main()
