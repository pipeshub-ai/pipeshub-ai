"""Tests for scripts/pf09_runtime_bench.py (run: python3 -m unittest discover -s scripts -p 'test_*.py').

The rule that picks the default runtime is pre-registered in ADR-OWD-03; these pin its thresholds, so the script
cannot drift from the ADR after the numbers are in."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pf09_runtime_bench as bench  # noqa: E402


def p50(runc: float, runsc: float, cold_runc: float = 0.5, cold_runsc: float = 0.5) -> dict:
    """Three equal workloads summing to ``runc`` and ``runsc`` seconds."""
    return {
        "runc": {"git_clone": runc / 3, "npm_ci": runc / 3, "pytest": runc / 3, "cold_start": cold_runc},
        "runsc": {"git_clone": runsc / 3, "npm_ci": runsc / 3, "pytest": runsc / 3, "cold_start": cold_runsc},
    }


class NearestRankTest(unittest.TestCase):
    def test_p50_and_p95_of_seven_samples(self):
        values = [7.0, 1.0, 6.0, 2.0, 5.0, 3.0, 4.0]
        self.assertEqual(bench.nearest_rank(values, 50), 4.0)
        self.assertEqual(bench.nearest_rank(values, 95), 7.0)
        self.assertEqual(bench.summary(values), {"p50": 4.0, "p95": 7.0, "n": 7})


class VerdictTest(unittest.TestCase):
    def test_at_most_twice_and_a_second_more_keeps_gvisor(self):
        decided = bench.verdict(p50(10, 20, 0.5, 1.5), conformance_ok=True)
        self.assertEqual(decided.rule, "keep_gvisor_default")
        self.assertAlmostEqual(decided.io_ratio, 2.0)
        self.assertAlmostEqual(decided.cold_start_delta_s, 1.0)

    def test_over_twice_keeps_it_with_a_follow_up(self):
        self.assertEqual(bench.verdict(p50(10, 21), conformance_ok=True).rule, "keep_with_follow_up")

    def test_a_cold_start_over_a_second_more_keeps_it_with_a_follow_up(self):
        self.assertEqual(bench.verdict(p50(10, 10, 0.5, 1.6), conformance_ok=True).rule, "keep_with_follow_up")

    def test_over_three_times_goes_back_to_the_owner(self):
        self.assertEqual(bench.verdict(p50(10, 31), conformance_ok=True).rule, "back_to_owner")

    def test_a_cold_start_over_three_seconds_more_goes_back_to_the_owner(self):
        self.assertEqual(bench.verdict(p50(10, 10, 0.5, 3.6), conformance_ok=True).rule, "back_to_owner")

    def test_an_engine_failing_only_on_gvisor_goes_back_to_the_owner_whatever_the_numbers(self):
        self.assertEqual(bench.verdict(p50(10, 10), conformance_ok=False).rule, "back_to_owner")


class HardenedTest(unittest.TestCase):
    def test_the_benchmark_sandbox_carries_the_brokers_hardening_and_no_network(self):
        flags = bench.hardened("runsc")
        for flag in ("--runtime=runsc", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                     "--init", "--cpus=2", "--network=none"):
            self.assertIn(flag, flags)


if __name__ == "__main__":
    unittest.main()
