"""scripts/heavy_result.py: an unrelated label's run can never turn a failure into a pass.

Run: python3 -m unittest discover -s scripts -p 'test_heavy_result.py'
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import heavy_result as hr  # noqa: E402


def _decide(heavy: bool, runs: list[dict], results: dict[int, list[tuple[str, str | None]]], needs: dict | None = None,
            wait: float = 600) -> tuple[bool, str]:
    """`results[id]` is that run's result over successive polls (the last one repeats)."""
    seen: dict[int, int] = {}
    now = [0.0]

    def result_of(run: dict) -> tuple[str, str | None]:
        i = seen.get(run["id"], 0)
        seen[run["id"]] = i + 1
        polls = results[run["id"]]
        return polls[min(i, len(polls) - 1)]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return hr.decide(heavy, needs or {}, 50, lambda: runs, result_of, wait, sleep=sleep, clock=lambda: now[0])


class HeavyResultTest(unittest.TestCase):
    def test_a_run_the_gate_acts_on_fails_on_a_failed_or_cancelled_job(self) -> None:
        self.assertTrue(_decide(True, [], {}, {"a": {"result": "success"}, "b": {"result": "skipped"}})[0])
        for bad in ("failure", "cancelled"):
            ok, why = _decide(True, [], {}, {"a": {"result": "success"}, "b": {"result": bad}})
            self.assertFalse(ok, bad)
            self.assertIn("b", why)

    def test_an_unrelated_label_after_a_failure_still_fails(self) -> None:
        runs = [{"id": 10}, {"id": 40}, {"id": 50}, {"id": 70}]
        ok, why = _decide(False, runs, {40: [("completed", "failure")], 10: [("completed", "success")]})
        self.assertFalse(ok)
        self.assertIn("run 40", why)

    def test_an_unrelated_label_after_a_pass_passes(self) -> None:
        self.assertTrue(_decide(False, [{"id": 40}], {40: [("completed", "success")]})[0])

    def test_no_earlier_run_passes(self) -> None:
        # A pull request with no ci: label may merge; later runs (id 70) are not earlier.
        self.assertTrue(_decide(False, [{"id": 70}], {})[0])

    def test_it_waits_for_an_earlier_run_still_going(self) -> None:
        polls = {40: [("in_progress", None), ("in_progress", None), ("completed", "failure")]}
        self.assertFalse(_decide(False, [{"id": 40}], polls)[0])
        polls = {40: [("queued", None), ("completed", "success")]}
        self.assertTrue(_decide(False, [{"id": 40}], polls)[0])

    def test_a_run_still_going_at_the_deadline_fails(self) -> None:
        ok, why = _decide(False, [{"id": 40}], {40: [("in_progress", None)]}, wait=60)
        self.assertFalse(ok)
        self.assertIn("still running", why)


if __name__ == "__main__":
    unittest.main()
