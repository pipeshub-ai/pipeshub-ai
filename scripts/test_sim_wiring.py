"""scripts/sim_wiring.py: the simulator-shard wiring of the integration workflow, and each mistake it must catch.

Each mutation below is one a reviewer can miss and no other guard sees; it either runs a simulator suite on the real
model, keeps the wrong cases, or leaves its simulator unstarted until the job fails after approval.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim_wiring  # noqa: E402

TEXT = sim_wiring.WORKFLOW.read_text(encoding="utf-8")
TIERS = json.loads(sim_wiring.SIM_TIERS.read_text(encoding="utf-8"))


def mutate(old: str, new: str, *, count: int = 1, last: bool = False) -> str:
    assert TEXT.count(old) >= count, old
    if last:
        index = TEXT.rindex(old)
        return TEXT[:index] + new + TEXT[index + len(old):]
    return TEXT.replace(old, new, count)


class SimWiring(unittest.TestCase):
    def assertCaught(self, text: str, words: str) -> None:
        found = sim_wiring.problems(text, TIERS)
        self.assertTrue(any(words in problem for problem in found), found)

    def test_the_workflow_is_wired(self) -> None:
        self.assertEqual(sim_wiring.problems(TEXT, TIERS), [])

    def test_a_simulator_flag_keyed_on_another_shard(self) -> None:
        self.assertCaught(
            mutate("NEEDS_ZAMMAD_SIM: ${{ (matrix.shard == 'connectors-6'", "NEEDS_ZAMMAD_SIM: ${{ (matrix.shard == 'connectors-2'"),
            "does not start the simulator on connectors-6",
        )

    def test_a_suite_without_a_simulator_flag(self) -> None:
        tiers = {**TIERS, "suites": {**TIERS["suites"], "nosim_sim": {"pr": "all", "nightly": "all"}}}
        found = sim_wiring.problems(TEXT, tiers)
        self.assertTrue(any("NEEDS_NOSIM_SIM" in problem for problem in found), found)

    def test_the_env_step_after_the_model_check(self) -> None:
        start = TEXT.index("      # A simulator shard (integration-tests/sim_tiers.json) runs on the fake model")
        end = TEXT.index("      # Stop before the stack build when the chosen provider's key is missing.")
        env_step = TEXT[start:end]
        without = TEXT[:start] + TEXT[end:]
        anchor = "        run: .github/scripts/check_ai_model_provider.sh\n"
        moved = without.replace(anchor, anchor + "\n" + env_step, 1)
        self.assertCaught(moved, "runs after")

    def test_tier_flags_dropped_from_one_pytest_call(self) -> None:
        self.assertCaught(
            mutate('pytest -m "$MARKERS" --sim-tier "$SIM_TIER" --sim-slot "$SIM_SLOT" -v', 'pytest -m "$MARKERS" -v', last=True),
            "without --sim-tier",
        )

    def test_tier_settings_moved_to_the_job_env(self) -> None:
        moved = mutate("      SHARD: ${{ matrix.shard }}\n", "      SHARD: ${{ matrix.shard }}\n      SIM_TIER: all\n")
        self.assertCaught(moved, "SIM_TIER is in the job env")

    def test_a_slot_from_the_clock_instead_of_the_setup_job(self) -> None:
        self.assertCaught(
            mutate("SIM_SLOT: ${{ needs.setup.outputs.sim_slot }}", "SIM_SLOT: ${{ github.run_number }}", last=True),
            "takes SIM_SLOT from elsewhere",
        )

    def test_no_slot_output_from_the_setup_job(self) -> None:
        self.assertCaught(mutate("      sim_slot: ${{ steps.pick.outputs.sim_slot }}\n", ""), "no sim_slot output")

    def test_the_fake_model_dropped_from_a_compose_line(self) -> None:
        self.assertCaught(mutate("$ZAMMAD_SIM_COMPOSE $FAKE_MODEL_COMPOSE up -d", "$ZAMMAD_SIM_COMPOSE up -d"), "not $FAKE_MODEL_COMPOSE")

    def test_a_simulator_flag_whose_dispatch_clause_names_another_marker(self) -> None:
        self.assertCaught(
            mutate("github.event.inputs.connectors == 'zammad_sim'", "github.event.inputs.connectors == 'zammad-sim'"),
            "manual run of zammad_sim",
        )

    def test_the_env_step_writing_somewhere_other_than_github_env(self) -> None:
        self.assertCaught(mutate('--connectors "$CONNECTORS_INPUT" >> "$GITHUB_ENV"', '--connectors "$CONNECTORS_INPUT" >> "$GITHUB_OUTPUT"'), "must be exactly")

    def test_the_env_step_made_conditional(self) -> None:
        self.assertCaught(
            mutate("      - name: Use the fake model on simulator shards\n", "      - name: Use the fake model on simulator shards\n        if: false\n"),
            "has an `if:`",
        )

    def test_the_tier_pinned_to_all(self) -> None:
        pinned = TEXT.replace(sim_wiring.TIER_BY_EVENT, "all")
        self.assertNotEqual(pinned, TEXT)
        self.assertCaught(pinned, "sets SIM_TIER to something other")

    def test_tier_flags_dropped_from_the_collect_only_pre_step(self) -> None:
        self.assertCaught(
            mutate('if ! pytest --collect-only -q -m "$MARKERS" --sim-tier "$SIM_TIER" --sim-slot "$SIM_SLOT"',
                   'if ! pytest --collect-only -q -m "$MARKERS"', last=True),
            "without --sim-tier",
        )

    def test_a_renamed_model_check_is_still_found_and_ordered(self) -> None:
        renamed = mutate("      - name: Check the AI model provider\n", "      - name: Check the model\n")
        self.assertEqual(sim_wiring.problems(renamed, TIERS), [])
        start = renamed.index("      # A simulator shard (integration-tests/sim_tiers.json) runs on the fake model")
        end = renamed.index("      # Stop before the stack build when the chosen provider's key is missing.")
        env_step, rest = renamed[start:end], renamed[:start] + renamed[end:]
        anchor = "        run: .github/scripts/check_ai_model_provider.sh\n"
        self.assertCaught(rest.replace(anchor, anchor + "\n" + env_step, 1), "runs after the AI model check")

    def test_a_missing_model_check_is_reported(self) -> None:
        self.assertCaught(
            mutate("        run: .github/scripts/check_ai_model_provider.sh\n", "        run: echo skipped\n"),
            "expected one step running .github/scripts/check_ai_model_provider.sh",
        )


    def test_a_simulator_flag_joined_with_and_instead_of_or(self) -> None:
        self.assertCaught(
            mutate("(matrix.shard == 'connectors-6' || (matrix.shard == 'dispatch'", "(matrix.shard == 'connectors-6' && (matrix.shard == 'dispatch'"),
            "NEEDS_ZAMMAD_SIM must be exactly",
        )

    def test_a_simulator_flag_negated(self) -> None:
        old = "github.event.inputs.connectors == 'zammad_sim')) && 'true' || 'false' }}"
        self.assertCaught(mutate(old, "github.event.inputs.connectors == 'zammad_sim')) && 'false' || 'true' }}"), "NEEDS_ZAMMAD_SIM must be exactly")

    def test_the_env_step_deleted(self) -> None:
        start = TEXT.index("      # A simulator shard (integration-tests/sim_tiers.json) runs on the fake model")
        end = TEXT.index("      # Stop before the stack build when the chosen provider's key is missing.")
        self.assertCaught(TEXT[:start] + TEXT[end:], "expected one step running scripts/sim_shard_env.py, found 0")

    def test_the_env_step_with_its_own_env_or_continue_on_error(self) -> None:
        step = "      - name: Use the fake model on simulator shards\n"
        for extra in ("        env:\n          SHARD: core\n", "        continue-on-error: true\n"):
            with self.subTest(extra=extra.strip()):
                self.assertCaught(mutate(step, step + extra), "the step running scripts/sim_shard_env.py has [")

    def test_a_second_tier_flag_that_overrides_the_first(self) -> None:
        old = 'pytest -m "$MARKERS" --sim-tier "$SIM_TIER" --sim-slot "$SIM_SLOT" -v'
        self.assertCaught(mutate(old, old.replace(" -v", " --sim-tier all -v"), last=True), "second --sim-tier")

    def test_a_pytest_called_by_path_still_needs_the_tier_flags(self) -> None:
        old = '          pytest -m "$MARKERS" --sim-tier "$SIM_TIER" --sim-slot "$SIM_SLOT" -v --tb=short \\\n'
        self.assertCaught(mutate(old, old + '          .venv/bin/pytest -m "$MARKERS" -q\n', last=True), "without --sim-tier")

    def test_the_fake_model_health_check_dropped_or_made_unconditional(self) -> None:
        start = TEXT.index("      - name: 'ArangoDB: Check the fake model stayed up'")
        end = TEXT.index("      - name: 'ArangoDB: Run Playwright e2e tests'")
        comment = TEXT.rindex("      # A simulator suite reads what was synced", 0, start)
        self.assertCaught(TEXT[:comment] + TEXT[end:], "expected a fake-model health check after each leg's tests")
        self.assertCaught(
            mutate("if: ${{ !cancelled() && env.SIM_JOB == '1' && steps.arango-ready.outcome == 'success' }}",
                   "if: ${{ !cancelled() && steps.arango-ready.outcome == 'success' }}"),
            "does not run on simulator jobs",
        )

    def test_a_health_check_moved_before_its_legs_tests(self) -> None:
        start = TEXT.index("      # A simulator suite reads what was synced")
        end = TEXT.index("      - name: 'Neo4j: Run Playwright e2e tests'")
        rest = TEXT[:start] + TEXT[end:]
        anchor = "      - name: 'Neo4j: Run integration tests'"
        moved = rest.replace(anchor, TEXT[start:end] + anchor, 1)
        self.assertCaught(moved, "runs before 'Neo4j: Run integration tests'")

    def test_both_health_checks_reading_one_legs_stack(self) -> None:
        old = "id=$(docker compose -f docker-compose.integration.arango.yml $FAKE_MODEL_COMPOSE ps -a -q fakellm)"
        self.assertCaught(mutate(old, old.replace("arango", "neo4j")), "the ArangoDB leg has 0")

    def test_a_health_check_that_ignores_the_models_failed_calls(self) -> None:
        old = "grep -c '^fake-model FAILED'"
        self.assertCaught(mutate(old, "grep -c '^fake-model OK'", last=True), "does not fail on the fake model's failed calls")


    def test_the_setup_job_without_read_access_to_its_run(self) -> None:
        old = "      # A re-run of all jobs reads this run's first day (Pick the setup).\n      actions: read\n"
        self.assertCaught(mutate(old, ""), "cannot read its run's first day")


if __name__ == "__main__":
    unittest.main()
