"""Which setup each integration run tests on.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nightly_setup as ns  # noqa: E402

GRAPHS = ("neo4j", "arango")  # every run covers both
WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "integration-tests.yml"


def week_from(start: date) -> list[dict[str, str]]:
    return [ns.setup_for(start + timedelta(days=i)) for i in range(7)]


class TestRotation(unittest.TestCase):
    def test_every_choice_runs_within_any_seven_nights(self) -> None:
        for offset in range(7):
            seen = {(part, s[part]) for s in week_from(date(2026, 9, 14) + timedelta(days=offset)) for part in s}
            wanted = {(part, value) for part, values in ns.CHOICES.items() for value in values}
            self.assertEqual(seen, wanted)

    def test_every_graph_pairs_with_every_choice_each_week(self) -> None:
        pairs = {(g, part, s[part]) for s in week_from(date(2026, 9, 14)) for part in s for g in GRAPHS}
        wanted = {(g, part, value) for g in GRAPHS for part, values in ns.CHOICES.items() for value in values}
        self.assertEqual(pairs, wanted)

    def test_the_default_setup_still_runs_most_nights(self) -> None:
        defaults = sum(s == ns.DEFAULT for s in week_from(date(2026, 9, 14)))
        self.assertGreaterEqual(defaults, 4)

    def test_rotation_only_uses_choices_the_stack_can_run(self) -> None:
        for overrides in ns.ROTATION.values():
            for part, value in overrides.items():
                self.assertIn(value, ns.CHOICES[part])
                self.assertIn((part, value), ns.NAMES)

    def test_a_known_night(self) -> None:
        self.assertEqual(ns.setup_for(date(2026, 9, 16)), {"message_broker": "kafka"})  # a Wednesday
        self.assertEqual(ns.setup_for(date(2026, 9, 17)), ns.DEFAULT)


class TestSimSlot(unittest.TestCase):
    """The slice of the rotating simulator suites each run keeps (integration-tests/sim_tiers.json)."""

    def test_every_monday_to_sunday_week_takes_every_slot_once(self) -> None:
        monday = date(2026, 11, 23)
        for week in range(60):  # across a year boundary and a 53-week year
            start = monday + timedelta(days=7 * week)
            slots = [ns.sim_slot(start + timedelta(days=i)) for i in range(7)]
            self.assertEqual(sorted(slots), list(range(ns.SIM_SLOTS)), start)

    def test_a_slot_moves_to_every_weekday_over_seven_weeks_so_it_meets_every_broker(self) -> None:
        for monday in (date(2026, 11, 23), date(2027, 12, 6)):
            weekdays = set()
            for week in range(7):
                start = monday + timedelta(days=7 * week)
                weekdays.add([ns.sim_slot(start + timedelta(days=i)) for i in range(7)].index(0))
            self.assertEqual(weekdays, set(range(7)), monday)
        kafka_nights = {iso for iso in range(1, 8) if ns.ROTATION.get(iso, {}).get("message_broker") == "kafka"}
        self.assertTrue(kafka_nights and len(kafka_nights) < 7, "the broker rotates by weekday")


class TestResolve(unittest.TestCase):
    def test_pull_requests_always_use_the_default(self) -> None:
        self.assertEqual(ns.resolve("pull_request", date(2026, 9, 16), {"message_broker": "kafka"}), ns.DEFAULT)

    def test_a_manual_run_gets_what_it_asked_for(self) -> None:
        self.assertEqual(ns.resolve("workflow_dispatch", date(2026, 9, 17), {"message_broker": "kafka"}), {"message_broker": "kafka"})
        self.assertEqual(ns.resolve("workflow_dispatch", date(2026, 9, 16), {"message_broker": ""}), ns.DEFAULT)

    def test_a_manual_run_with_an_unknown_choice_says_what_to_pick(self) -> None:
        with self.assertRaises(ValueError) as caught:
            ns.resolve("workflow_dispatch", date(2026, 9, 17), {"message_broker": "rabbitmq"})
        self.assertIn("Pick one of: redis, kafka", str(caught.exception))


class TestMain(unittest.TestCase):
    def test_writes_outputs_and_a_plain_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out, summary = Path(tmp, "out"), Path(tmp, "summary")
            env = {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                self.assertEqual(ns.main(["--event", "schedule", "--date", "2026-09-16"]), 0)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
            self.assertEqual(lines["message_broker"], "kafka")
            self.assertEqual(lines["label"], "Kafka message queue")
            self.assertEqual(lines["is_default"], "false")
            self.assertEqual(lines["sim_slot"], str(ns.sim_slot(date(2026, 9, 16))))
            self.assertIn("Kafka message queue, on both Neo4j and ArangoDB", summary.read_text())

    def test_an_unknown_choice_exits_non_zero(self) -> None:
        self.assertEqual(ns.main(["--event", "workflow_dispatch", "--message-broker", "rabbitmq"]), 1)



class TheSetupStep(unittest.TestCase):
    """The workflow's 'Pick the setup' step, run the way the runner runs it, with `gh` replaced by a stub."""

    def run_step(self, attempt: str, gh_body: str) -> tuple[dict[str, str], str, Path]:
        job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["setup"]
        step = next(step for step in job["steps"] if step.get("id") == "pick")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        stubs = tmp / "bin"
        stubs.mkdir()
        gh = stubs / "gh"
        gh.write_text(f"#!/bin/sh\necho called >> '{tmp / 'gh-calls'}'\n{gh_body}\n")
        gh.chmod(0o755)
        env = {
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "EVENT_NAME": "schedule", "REQUESTED_BROKER": "", "RUN_ATTEMPT": attempt, "GH_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "1",
            "GITHUB_OUTPUT": str(tmp / "output"), "GITHUB_STEP_SUMMARY": str(tmp / "summary"),
        }
        result = subprocess.run(
            ["bash", "-e", "-c", step["run"]], cwd=WORKFLOW.parents[2], env=env, capture_output=True, text=True,
            timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = (tmp / "output").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line), result.stdout + result.stderr, tmp / "gh-calls"

    def test_a_rerun_of_every_job_keeps_the_first_attempts_day(self) -> None:
        today = datetime.now(UTC).date()
        first = today - timedelta(days=1)
        outputs, _, _ = self.run_step("2", f"echo {first.isoformat()}")
        self.assertEqual(outputs["sim_slot"], str(ns.sim_slot(first)))
        self.assertNotEqual(outputs["sim_slot"], str(ns.sim_slot(today)))
        self.assertEqual(outputs["message_broker"], ns.setup_for(first)["message_broker"])

    def test_a_first_attempt_takes_today_and_asks_github_nothing(self) -> None:
        outputs, _, calls = self.run_step("1", "exit 1")
        self.assertEqual(outputs["sim_slot"], str(ns.sim_slot(datetime.now(UTC).date())))
        self.assertFalse(calls.exists(), "the first attempt called gh")

    def test_a_rerun_that_cannot_read_its_day_warns_and_takes_today(self) -> None:
        outputs, log, _ = self.run_step("3", "exit 1")
        self.assertEqual(outputs["sim_slot"], str(ns.sim_slot(datetime.now(UTC).date())))
        self.assertIn("::warning::", log)

if __name__ == "__main__":
    unittest.main()
