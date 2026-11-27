"""WP-70 DEEPFINISH: an engine turn's allowance (WP70-STEPS, WP70-TELL, WP70-SYNTH, WP70-OWNLIMIT).

A deep research run: the release profile's 50-step cap ended Codex's research at USD 0.64 of its USD 5 budget,
nothing told the engine about any limit, and the turn was cut before it wrote a word of the report.
"""

from __future__ import annotations

import json

import pytest

from harness_core.budget.run_budget import MIN_BUDGET_STEPS, Allowance, step_backstop
from harness_core.engines.allowance import allowance_note, synthesis_text
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)

RUN = "run-wp70"


# ---- WP70-STEPS: the money decides; the steps are a backstop scaled to it ----


def test_wp70_steps_a_metered_run_with_a_budget_gets_steps_scaled_to_it_within_its_profile() -> None:
    # Run 3: USD 5 under the release profile: 500 steps, not 50.
    assert step_backstop(1000, 5.0, metered=True) == 500
    # A small budget keeps a floor, so one model call's price never ends a turn after a handful of steps.
    assert step_backstop(1000, 0.05, metered=True) == MIN_BUDGET_STEPS
    # An admin's narrowing still binds: the backstop never exceeds what the profile allows.
    assert step_backstop(20, 5.0, metered=True) == 20


def test_wp70_steps_an_unmetered_run_or_one_without_a_budget_keeps_its_profiles_steps() -> None:
    assert step_backstop(50, 5.0, metered=False) == 50
    assert step_backstop(50, None, metered=True) == 50
    assert step_backstop(None, None, metered=False) is None


def test_wp70_steps_the_release_profile_bounds_steps_for_research_not_for_a_chat_reply() -> None:
    from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS

    release = RELEASE_DEFAULTS.engine_profile.limits.max_steps
    assert release is not None and release.root >= 500
    # The release's own USD 5 per-run ceiling is then what a research turn meets first.
    assert step_backstop(release.root, RELEASE_DEFAULTS.budgets.max_cost_usd, metered=True) == 500


# ---- WP70-SYNTH: a writing reserve on every limit ----


def test_wp70_synth_the_window_opens_within_its_share_of_each_limit() -> None:
    allowance = Allowance(steps=500, wall_s=1800, usd=5.0)
    assert allowance.synthesis_due(steps=424, elapsed_s=0) is None
    assert allowance.synthesis_due(steps=425, elapsed_s=0) == "steps"
    assert allowance.synthesis_due(steps=10, elapsed_s=1529) is None
    assert allowance.synthesis_due(steps=10, elapsed_s=1530) == "wall_time"
    assert allowance.synthesis_due(steps=10, elapsed_s=10, usd_left_share=0.2) is None
    assert allowance.synthesis_due(steps=10, elapsed_s=10, usd_left_share=0.15) == "budget"
    # A small allowance still keeps a few steps and two minutes back, never all of it.
    small = Allowance(steps=10, wall_s=60, usd=None)
    assert small.synthesis_steps() == 8 and small.synthesis_wall_s() == 30
    assert Allowance(None, None, None).synthesis_due(steps=10_000, elapsed_s=1e9) is None


def test_wp70_synth_the_engine_is_told_what_to_write_in_plain_words() -> None:
    told = synthesis_text("steps", "75 steps", "report.md")
    assert told.startswith("Stop gathering") and "out/report.md" in told and "75 steps" in told
    assert "Cite each finding" in told and "naming the file" in told
    assert "Write your answer now" in synthesis_text("budget", "USD 0.40", None)


# ---- WP70-TELL: the engine knows its allowance ----


def test_wp70_tell_the_note_names_each_bound_and_the_stop_signal() -> None:
    note = allowance_note(Allowance(steps=500, wall_s=1800, usd=5.0))
    assert "500 model steps, about 30 minutes and USD 5.00 of model use" in note
    assert "told to stop gathering" in note and "cite every source" in note
    # That run's Codex created a "Research in progress" file and gathered for 9 minutes without writing to it.
    assert "write each finding into the file the task asks for as you make it" in note
    assert "never just a placeholder" in note
    assert allowance_note(Allowance(None, None, None)) == ""


async def test_wp70_tell_the_engines_instructions_carry_its_allowance() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter())
    world.admit(RUN, "say hello", spec=engine_run_spec(engine_profile(max_steps=40, max_wall_s=600)))
    assert (await world.attempt(RUN)).status == "completed"
    system = world.gateway.model_calls[0].system
    assert "Your allowance for this task is 40 model steps and about 10 minutes" in system


# ---- WP70-SYNTH on a running turn ----


def _lookups(n: int) -> list[str]:
    return [f'tool lookup {json.dumps({"page": i})}' for i in range(n)]


async def test_wp70_synth_near_the_step_limit_the_engine_is_steered_and_its_calls_refused() -> None:
    """Steps 1-12 of a 20-step allowance gather; at the writing reserve (8 steps) the engine is steered to write, and
    each later gateway call is refused with the same words instead of running."""
    world = EngineWorld(adapter=ScriptedEngineAdapter())
    script = "\n".join([*_lookups(15), "say report written"])
    world.admit(RUN, f"Write report.md about it.\n{script}", spec=engine_run_spec(engine_profile(max_steps=20)))
    assert (await world.attempt(RUN)).status == "completed"
    # The step's usage reaches the controller as an event, so the next step may start before it is counted: the
    # window opens at the twelfth step, or one step later.
    looked_up = sum(n for (tool, _), n in world.effects.items() if tool == "lookup")
    assert looked_up in (12, 13), "the calls after the window opened never ran"
    last = world.gateway.model_calls[-1].messages
    steered = [m.content for m in last if m.role == "user" and m.content.startswith("Stop gathering")]
    assert len(steered) == 1 and "out/report.md" in steered[0]
    refused = [m.content for m in last if m.role == "tool" and m.content.startswith("Stop gathering")]
    assert len(refused) == 15 - looked_up


async def test_wp70_synth_an_engine_that_cannot_steer_reads_it_as_its_next_calls_refusal() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(can_steer=False))
    script = "\n".join([*_lookups(14), "say done"])
    world.admit(RUN, script, spec=engine_run_spec(engine_profile(max_steps=20)))
    assert (await world.attempt(RUN)).status == "completed"
    last = world.gateway.model_calls[-1].messages
    assert not any(m.role == "user" and m.content.startswith("Stop gathering") for m in last)
    assert any(m.role == "tool" and m.content.startswith("Stop gathering") for m in last)


# ---- WP70-OWNLIMIT: the engine's own step bound is a step limit, not a lost turn ----


async def test_wp70_ownlimit_an_engine_that_stops_at_its_own_step_bound_completes_cut_short() -> None:
    """opencode and ACP agents get the profile's steps as their own bound. An engine that ended its turn there was
    taken for a lost turn and driven again; it is the step limit, and the run completes flagged."""
    world = EngineWorld(adapter=ScriptedEngineAdapter())
    world.admit(RUN, "\n".join([*_lookups(30), "say never reached"]), spec=engine_run_spec(engine_profile(max_steps=12)))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed", disposition
    completed = [e.data for e in world.events(RUN) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "turns"}]
    answer = await world.plain_answer(RUN)
    assert "step limit" in answer


@pytest.mark.parametrize("steps", [None, 7])
def test_wp70_synth_a_tiny_or_unbounded_allowance_never_reserves_every_step(steps: int | None) -> None:
    reserve = Allowance(steps=steps, wall_s=None, usd=None).synthesis_steps()
    assert reserve is None if steps is None else 0 < reserve < steps
