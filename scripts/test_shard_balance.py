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

    def test_a_held_out_connector_must_stay_out_of_core(self) -> None:
        pytest_ini = PYTEST_INI.replace(
            "    cleanup:",
            "    cifs: marks tests specific to the CIFS/SMB1 connector\n    cleanup:",
        )
        missing, _ = run(pytest_ini=pytest_ini)
        self.assertTrue(any("cifs" in p and "fall into core" in p for p in missing), missing)
        held_in_shard, _ = run(
            workflow=WORKFLOW.replace('"gamma"', '"gamma or cifs"') + "\n# not cifs\n",
            pytest_ini=pytest_ini,
        )
        self.assertTrue(any("still selects" in p for p in held_in_shard), held_in_shard)
        # A comment is not an exclusion. Both core jobs still select cifs.
        comment_only = WORKFLOW + (
            '            core)         MARKERS="integration and not (alpha or beta or gamma)" ;;\n'
            '            core)         MARKERS="integration and not (alpha or beta or gamma) and not cifs" ;;\n'
            "# not cifs\n"
        )
        commented, _ = run(workflow=comment_only, pytest_ini=pytest_ini)
        self.assertTrue(any("cifs" in p and "fall into core" in p for p in commented), commented)
        excluded = WORKFLOW + (
            '            core)         MARKERS="integration and not (alpha or beta or gamma) and not cifs" ;;\n'
            '            core)         MARKERS="integration and not (alpha or beta or gamma) and not cifs" ;;\n'
        )
        allowed, _ = run(workflow=excluded, pytest_ini=pytest_ini)
        self.assertEqual(allowed, [])
        # "not cifs" as text is not enough: the or-branch still selects it.
        disjunction = WORKFLOW + (
            '            core)         MARKERS="not cifs or cifs" ;;\n'
            '            core)         MARKERS="integration and not cifs" ;;\n'
        )
        or_selects, _ = run(workflow=disjunction, pytest_ini=pytest_ini)
        self.assertTrue(any("cifs" in p and "fall into core" in p for p in or_selects), or_selects)
        stronger = WORKFLOW + (
            '            core)         MARKERS="integration and not (alpha or cifs)" ;;\n'
            '            core)         MARKERS="integration and not (alpha or cifs)" ;;\n'
        )
        strong_ok, _ = run(workflow=stronger, pytest_ini=pytest_ini)
        self.assertEqual(strong_ok, [])

    def test_the_demo_shard_runs_the_demo_exactly_once(self) -> None:
        with_job = WORKFLOW.replace('"core"]', '"core","demo"]')
        core = '            core)         MARKERS="integration and not (alpha or beta or gamma){}" ;;\n'
        case = '            demo)         MARKERS="demo" ;;\n'

        def step(*lines: str) -> str:
            return '          case "$SHARD" in\n' + "".join(lines) + "          esac\n"

        sound = with_job + step(core.format(" and not demo"), case) * 2
        self.assertEqual(run(workflow=sound)[0], [])
        twice, _ = run(workflow=with_job + step(core.format(""), case) * 2)
        self.assertTrue(any("run twice" in p for p in twice), twice)
        one_leg, _ = run(workflow=with_job + step(core.format(" and not demo"), case) + step(core.format(" and not demo")))
        self.assertTrue(any("unknown shard" in p for p in one_leg), one_leg)
        # Two in one step and none in the other still totals two; the second step fails.
        lopsided = with_job + step(core.format(" and not demo"), case, case) + step(core.format(" and not demo"))
        self.assertTrue(any("unknown shard" in p for p in run(workflow=lopsided)[0]))
        # The shell reads `MARKERS="demo"extra` as the marker "demoextra".
        suffixed = with_job + step(core.format(" and not demo"), case.replace('"demo" ;;', '"demo"extra ;;')) * 2
        self.assertTrue(any("unknown shard" in p for p in run(workflow=suffixed)[0]))
        # Named only in the one-marker dispatch list, the demo job never runs on the nightly.
        dispatch_only = WORKFLOW.replace("'[\"dispatch\"]'", "'[\"dispatch\",\"demo\"]'")
        self.assertNotEqual(dispatch_only, WORKFLOW)
        only_dispatch, _ = run(workflow=dispatch_only + step(core.format(" and not demo"), case) * 2)
        self.assertTrue(any("stop running" in p for p in only_dispatch), only_dispatch)
        dropped, _ = run(workflow=WORKFLOW + step(core.format(" and not demo")) * 2)
        self.assertTrue(any("stop running" in p for p in dropped), dropped)
        self.assertEqual(run(workflow=WORKFLOW + step(core.format("")) * 2)[0], [])

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

    def test_the_ai_agents_shard_never_runs_on_a_pull_request(self) -> None:
        """Every ai_agents test costs model calls; the nightly runs them, a PR must not."""
        workflow = balance.WORKFLOW.read_text(encoding="utf-8")
        line = balance._MATRIX_LINE.search(workflow).group(0)
        pr_list = line.split("github.event_name == 'pull_request_target' && ", 1)[1].split("'", 2)[1]
        self.assertNotIn("ai_agents", pr_list)
        self.assertIn("ai_agents", balance.matrix_solo_shards(workflow))


if __name__ == "__main__":
    unittest.main()
