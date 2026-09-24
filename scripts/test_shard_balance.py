"""What the shard checker must catch, and that this repo's own split is sound.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shard_balance as balance  # noqa: E402

MATRIX = (
    '        shard: ${{ fromJSON(... && \'["dispatch"]\' || '
    '\'["connectors-1","connectors-2","core"]\') }}\n'
)

WORKFLOW = """
        shard: ${{ fromJSON(... && '["dispatch"]' || '["connectors-1","connectors-2","core"]') }}
      CONN_SHARD_1: "alpha or beta"
      CONN_SHARD_2: "gamma"
"""

PYTEST_INI = """
markers =
    integration: marks tests as integration tests (require external services)
    alpha: marks tests specific to Alpha connector
    beta: marks tests specific to Beta connector
    gamma: marks tests specific to Gamma connector
    cleanup: cross-store cleanup scenarios

addopts =
    -v
"""

MINUTES = {"alpha": 30.0, "beta": 20.0, "gamma": 50.0}


def run(workflow=WORKFLOW, pytest_ini=PYTEST_INI, minutes=None):
    return balance.check(workflow, pytest_ini, MINUTES if minutes is None else minutes)


class TestReadsTheSplit(unittest.TestCase):
    def test_markers_are_grouped_by_shard(self) -> None:
        self.assertEqual(
            balance.shard_markers(WORKFLOW),
            {"connectors-1": ["alpha", "beta"], "connectors-2": ["gamma"]},
        )

    def test_only_connector_markers_must_be_assigned(self) -> None:
        markers = balance.declared_markers(PYTEST_INI)
        self.assertEqual(balance.connector_markers(markers), {"alpha", "beta", "gamma"})
        self.assertIn("cleanup", markers)


class TestCatchesMistakes(unittest.TestCase):
    def test_a_balanced_split_passes(self) -> None:
        problems, _ = run()
        self.assertEqual(problems, [])

    def test_a_connector_in_no_shard_lands_on_core(self) -> None:
        workflow = MATRIX.replace('"connectors-2",', '') + '      CONN_SHARD_1: "alpha or beta"\n'
        problems, _ = run(workflow=workflow)
        self.assertTrue(
            any("gamma" in p and "fall into `core`" in p for p in problems), problems
        )

    def test_a_connector_in_two_shards_is_reported(self) -> None:
        workflow = MATRIX + '      CONN_SHARD_1: "alpha or beta"\n      CONN_SHARD_2: "beta or gamma"\n'
        problems, _ = run(workflow=workflow)
        self.assertTrue(any("run twice" in p for p in problems), problems)

    def test_a_renamed_marker_is_reported(self) -> None:
        workflow = MATRIX + '      CONN_SHARD_1: "alpha or beeta"\n      CONN_SHARD_2: "gamma"\n'
        problems, _ = run(workflow=workflow)
        self.assertTrue(any("beeta" in p and "pytest.ini" in p for p in problems), problems)
        # The real marker is now unassigned too, so both halves of the rename show up.
        self.assertTrue(any("'beta' is in no shard" in p for p in problems), problems)

    def test_a_lopsided_split_is_reported(self) -> None:
        workflow = MATRIX + '      CONN_SHARD_1: "alpha or beta or gamma"\n      CONN_SHARD_2: ""\n'
        problems, _ = run(workflow=workflow)
        self.assertTrue(any("the average shard" in p for p in problems), problems)

    def test_a_shard_with_no_matching_job_is_reported(self) -> None:
        """The real silent skip: core excludes the suites and no job selects them."""
        workflow = MATRIX + WORKFLOW.split("}}\n", 1)[1] + '      CONN_SHARD_3: "delta"\n'
        pytest_ini = PYTEST_INI.replace(
            "    cleanup:", "    delta: marks tests specific to Delta connector\n    cleanup:"
        )
        problems, _ = run(workflow=workflow, pytest_ini=pytest_ini)
        self.assertTrue(
            any("CONN_SHARD_3" in p and "stop running" in p for p in problems), problems
        )

    def test_a_job_with_no_marker_list_is_reported(self) -> None:
        workflow = MATRIX.replace('"connectors-2"', '"connectors-2","connectors-3"') + (
            '      CONN_SHARD_1: "alpha or beta"\n      CONN_SHARD_2: "gamma"\n'
        )
        problems, _ = run(workflow=workflow)
        self.assertTrue(
            any("connectors-3" in p and "empty marker expression" in p for p in problems),
            problems,
        )

    def test_a_shard_naming_a_broad_marker_is_reported(self) -> None:
        """`integration` or `resilience` in a shard would pull in tests core excludes."""
        for broad in ("integration", "resilience"):
            pytest_ini = PYTEST_INI.replace(
                "    cleanup:", f"    {broad}: restarts stack services mid-run\n    cleanup:"
            ) if broad == "resilience" else PYTEST_INI
            workflow = MATRIX + (
                f'      CONN_SHARD_1: "alpha or beta or {broad}"\n'
                '      CONN_SHARD_2: "gamma"\n'
            )
            problems, _ = run(workflow=workflow, pytest_ini=pytest_ini)
            self.assertTrue(
                any(broad in p and "not a single connector's marker" in p for p in problems),
                f"{broad}: {problems}",
            )

    def test_an_unmeasured_suite_is_named_but_allowed(self) -> None:
        # beta has no measured time; the two shards still weigh the same without it.
        problems, report = run(minutes={"alpha": 30.0, "gamma": 30.0})
        self.assertEqual(problems, [])
        self.assertTrue(any("no measured time yet" in line and "beta" in line for line in report))


class TestThisRepo(unittest.TestCase):
    """The drift guard: fails when someone adds a connector and forgets a shard."""

    def test_every_connector_runs_once_and_the_shards_are_balanced(self) -> None:
        measured = json.loads(balance.DURATIONS.read_text(encoding="utf-8"))
        problems, report = balance.check(
            balance.WORKFLOW.read_text(encoding="utf-8"),
            balance.PYTEST_INI.read_text(encoding="utf-8"),
            measured["minutes"],
            measured.get("_core_minutes"),
        )
        self.assertEqual(problems, [], "\n".join(report + problems))

    def test_the_shard_holding_nextcloud_starts_its_containers(self) -> None:
        """nextcloud and bookstack need the selfhosted-sources compose profile."""
        workflow = balance.WORKFLOW.read_text(encoding="utf-8")
        shards = balance.shard_markers(workflow)
        owners = {shard for shard, names in shards.items()
                  if "nextcloud" in names or "bookstack" in names}
        self.assertEqual(len(owners), 1, f"split across shards: {owners}")
        owner = owners.pop()
        self.assertIn(
            f"matrix.shard == '{owner}'",
            workflow,
            f"{owner} runs nextcloud/bookstack but does not start selfhosted-sources",
        )


if __name__ == "__main__":
    unittest.main()
