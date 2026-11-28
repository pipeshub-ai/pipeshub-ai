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

    def test_an_unassigned_connector_is_reported(self) -> None:
        workflow = MATRIX + '      CONN_SHARD_1: "alpha"\n      CONN_SHARD_2: "gamma"\n'
        problems, _ = run(workflow=workflow)
        self.assertTrue(
            any("'beta' is in no shard" in p and "Add it to a CONN_SHARD_* line" in p
                for p in problems),
            problems,
        )

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

    def _with_a_simulator_shard(self, minutes: float):
        workflow = (
            '        shard: ${{ fromJSON(... && \'["dispatch"]\' || '
            '\'["connectors-1","connectors-2","connectors-3","core"]\') }}\n'
            + WORKFLOW.split("}}\n", 1)[1] + '      CONN_SHARD_3: "delta_sim"\n'
        )
        pytest_ini = PYTEST_INI.replace(
            "    cleanup:", "    delta_sim: Delta connector against the Delta simulator\n    cleanup:"
        )
        return workflow, pytest_ini, {**MINUTES, "delta_sim": minutes}

    def test_a_small_simulator_shard_is_left_out_of_the_average(self) -> None:
        workflow, pytest_ini, minutes = self._with_a_simulator_shard(5.0)
        # Counted in the average, the 5-minute shard makes the balanced live shards look lopsided.
        counted, _ = balance.check(workflow, pytest_ini, minutes)
        self.assertTrue(any("the average shard" in p for p in counted), counted)
        problems, report = balance.check(workflow, pytest_ini, minutes, simulator_shards=frozenset({"connectors-3"}))
        self.assertEqual(problems, [], "\n".join(report))
        self.assertTrue(any("connectors-3" in line and "simulator shard" in line for line in report), report)

    def test_a_simulator_shard_over_its_ceiling_is_reported(self) -> None:
        workflow, pytest_ini, minutes = self._with_a_simulator_shard(balance.SIM_SHARD_MAX_MINUTES + 1)
        problems, _ = balance.check(workflow, pytest_ini, minutes, simulator_shards=frozenset({"connectors-3"}))
        self.assertTrue(any("is a simulator shard" in p for p in problems), problems)

    def test_the_simulator_shards_come_from_sim_tiers(self) -> None:
        self.assertEqual(balance.sim_shards(), frozenset(json.loads(balance.SIM_TIERS.read_text())["sim_shards"]))
        self.assertEqual(balance.sim_shards(Path("/nonexistent/sim_tiers.json")), frozenset())

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
            balance.sim_shards(),
        )
        self.assertEqual(problems, [], "\n".join(report + problems))

    def _problems_with(self, old: str, new: str, count: int = 1) -> list[str]:
        text = balance.WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count(old), count, old)
        measured = json.loads(balance.DURATIONS.read_text(encoding="utf-8"))
        problems, _ = balance.check(
            text.replace(old, new, count), balance.PYTEST_INI.read_text(encoding="utf-8"),
            measured["minutes"], measured.get("_core_minutes"), balance.sim_shards(),
        )
        return problems

    def test_core_that_stops_excluding_a_shard_is_caught_in_either_step(self) -> None:
        for count in (2, 1):  # both test steps, or one
            with self.subTest(steps=count):
                problems = self._problems_with(" and not ($CONN_SHARD_5)", "", count)
                self.assertTrue(any("core expression selects" in p and "connectors-5" in p for p in problems), problems)

    def test_core_that_also_selects_core_cases_of_simulator_suites_is_caught(self) -> None:
        """A test of zammad_sim is also marked sim_core and integration: core must leave it out with those too."""
        problems = self._problems_with(
            ' and not demo and not ai_agents" ;;', ' and not demo and not ai_agents or (sim_core and not demo)" ;;', 2,
        )
        self.assertTrue(any("core expression selects 'zammad_sim'" in p for p in problems), problems)

    def test_a_shard_missing_from_the_full_run_or_it_all_list_is_caught(self) -> None:
        nightly = self._problems_with('"connectors-5","connectors-6","core","demo","ai_agents"]', '"connectors-6","core","demo","ai_agents"]')
        self.assertTrue(any("full-run list" in p and "connectors-5" in p for p in nightly), nightly)
        it_all = self._problems_with('"connectors-5","connectors-6","core","demo"]', '"connectors-6","core","demo"]')
        self.assertTrue(any("it-all list" in p and "connectors-5" in p for p in it_all), it_all)

    def test_a_case_statement_spelled_with_braces_is_still_read(self) -> None:
        text = balance.WORKFLOW.read_text(encoding="utf-8")
        last = text.rindex('case "$SHARD" in')
        braced = text[:last] + 'case "${SHARD}" in' + text[last + len('case "$SHARD" in'):]
        measured = json.loads(balance.DURATIONS.read_text(encoding="utf-8"))
        ini = balance.PYTEST_INI.read_text(encoding="utf-8")
        self.assertEqual(balance.check(braced, ini, measured["minutes"], None, balance.sim_shards())[0], [])
        # ...and its core is still checked: drop that step's exclusion of connectors-6.
        core_end = braced.rindex(" and not ($CONN_SHARD_6)")
        broken = braced[:core_end] + braced[core_end + len(" and not ($CONN_SHARD_6)"):]
        problems = balance.check(broken, ini, measured["minutes"], None, balance.sim_shards())[0]
        self.assertTrue(any("Test step 2's core expression selects 'zammad_sim'" in p for p in problems), problems)

    def test_a_step_this_check_cannot_read_is_reported(self) -> None:
        text = balance.WORKFLOW.read_text(encoding="utf-8")
        last = text.rindex('case "$SHARD" in')
        hidden = text[:last] + "case $SHARD in" + text[last + len('case "$SHARD" in'):]
        measured = json.loads(balance.DURATIONS.read_text(encoding="utf-8"))
        problems = balance.check(
            hidden, balance.PYTEST_INI.read_text(encoding="utf-8"), measured["minutes"], None, balance.sim_shards(),
        )[0]
        self.assertTrue(any("sit outside a `case" in p for p in problems), problems)

    def test_a_core_line_with_many_shards_is_still_read_exactly(self) -> None:
        """The core expression names every shard; past a dozen names the old check gave up and reported false
        problems (cifs, snowflake and demo 'in core'). Here with ten more shards than the repo has today."""
        many = " ".join(f"and not ($CONN_SHARD_{n})" for n in range(1, 17))
        core = f"integration {many} and not zendesk and not storage and not cifs and not snowflake and not demo and not ai_agents"
        for name in ("cifs", "snowflake", "demo", "ai_agents"):
            self.assertTrue(balance._always_excludes(core, name), name)
        self.assertFalse(balance._always_excludes(core.replace(" and not cifs", ""), "cifs"))
        self.assertFalse(balance._always_excludes("integration and (not cifs or demo)", "cifs"))

    def test_a_test_step_without_a_shard_line_is_caught(self) -> None:
        text = balance.WORKFLOW.read_text(encoding="utf-8")
        line = '            connectors-6) MARKERS="$CONN_SHARD_6" ;;\n'
        last = text.rindex(line)
        mutated = text[:last] + text[last + len(line):]
        measured = json.loads(balance.DURATIONS.read_text(encoding="utf-8"))
        problems, _ = balance.check(
            mutated, balance.PYTEST_INI.read_text(encoding="utf-8"), measured["minutes"], None, balance.sim_shards(),
        )
        self.assertTrue(any("Test step 2 has no `connectors-6)" in p for p in problems), problems)

    def test_a_shard_line_with_text_after_its_quote_is_caught(self) -> None:
        """`MARKERS="$CONN_SHARD_6"extra` gives the shell the marker `zammad_simextra`, so that job selects nothing."""
        text = balance.WORKFLOW.read_text(encoding="utf-8")
        line = '            connectors-6) MARKERS="$CONN_SHARD_6" ;;\n'
        last = text.rindex(line)
        mutated = text[:last] + line.replace('" ;;', '"extra ;;') + text[last + len(line):]
        measured = json.loads(balance.DURATIONS.read_text(encoding="utf-8"))
        problems, _ = balance.check(
            mutated, balance.PYTEST_INI.read_text(encoding="utf-8"), measured["minutes"], None, balance.sim_shards(),
        )
        self.assertTrue(any("Test step 2 has no `connectors-6)" in p for p in problems), problems)

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
        pr_list = line.split("github.event_name == 'pull_request' && ", 1)[1].split("'", 2)[1]
        self.assertNotIn("ai_agents", pr_list)
        self.assertIn("ai_agents", balance.matrix_solo_shards(workflow))


if __name__ == "__main__":
    unittest.main()
