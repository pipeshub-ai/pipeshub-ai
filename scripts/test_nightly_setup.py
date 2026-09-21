"""Which setup each integration run tests on.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nightly_setup as ns  # noqa: E402

GRAPHS = ("neo4j", "arango")  # every run covers both


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


class TestResolve(unittest.TestCase):
    def test_pull_requests_always_use_the_default(self) -> None:
        self.assertEqual(ns.resolve("pull_request_target", date(2026, 9, 16), {"message_broker": "kafka"}), ns.DEFAULT)

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
            self.assertIn("Kafka message queue, on both Neo4j and ArangoDB", summary.read_text())

    def test_an_unknown_choice_exits_non_zero(self) -> None:
        self.assertEqual(ns.main(["--event", "workflow_dispatch", "--message-broker", "rabbitmq"]), 1)


if __name__ == "__main__":
    unittest.main()
