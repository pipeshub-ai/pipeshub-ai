#!/usr/bin/env python3
"""Check the integration workflow's simulator-shard wiring against integration-tests/sim_tiers.json.

Each rule is a line whose loss runs a simulator suite on the wrong model, with the wrong cases, or not at all,
and that no other guard sees (shard_balance.py checks the shard lists and core's exclusions):

- each listed suite's simulator flag (``NEEDS_<SUITE>``) starts its simulator on the suite's own shard and on a
  manual run of that suite;
- the env step that puts a simulator shard on the fake model runs unconditionally, writes to GITHUB_ENV, and runs
  before the AI model check (steps are found by what they run, not by their names);
- both test steps pass the tier flags to every pytest call, the collect-only pre-step included, and set
  ``SIM_TIER`` (to the tier each event uses) and ``SIM_SLOT`` (from the ``setup`` job) at step level, where no env
  file loaded into GITHUB_ENV can override them;
- every docker compose line that adds a simulator's compose file also adds the fake model's;
- each leg checks, after its own tests and on its own stack, that the fake model is still running, healthy and
  never restarted, and that its log holds no failed call.

A rule whose line it cannot find is a problem too, so a renamed or reshaped step fails the check instead of
passing it.

Run: python3 scripts/sim_wiring.py   (exit 1 and one line per problem)
Test: python3 -m unittest discover -s scripts -p 'test_sim_wiring.py'
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "integration-tests.yml"
SIM_TIERS = REPO / "integration-tests" / "sim_tiers.json"
JOB = "integration-tests"
TEST_STEPS = ("Neo4j: Run integration tests", "ArangoDB: Run integration tests")
ENV_STEP_RUNS = 'python3 scripts/sim_shard_env.py --shard "$SHARD" --connectors "$CONNECTORS_INPUT" >> "$GITHUB_ENV"'
PROVIDER_CHECK_RUNS = ".github/scripts/check_ai_model_provider.sh"
TIER_FLAGS = '--sim-tier "$SIM_TIER" --sim-slot "$SIM_SLOT"'
SLOT_FROM_SETUP = "${{ needs.setup.outputs.sim_slot }}"
TIER_BY_EVENT = (
    "${{ (github.event_name == 'pull_request' && 'pr') || (github.event_name == 'schedule' && 'nightly') "
    "|| github.event.inputs.sim_tier || 'all' }}"
)
_PYTEST_CALL = re.compile(r"(?:^|[\s!;&|(/])pytest\s")
HEALTH_CHECK = "ps -a -q fakellm"
# Each test step and the compose file of its stack, which that leg's health check must read.
LEG_STACKS = dict(zip(TEST_STEPS, ("docker-compose.integration.neo4j.yml", "docker-compose.integration.arango.yml")))
FAILURE_MARK = "fake-model FAILED"  # FAILED in integration-tests/fake_model/server.py
FAILED_CALLS_CHECK = f"grep -c '^{FAILURE_MARK}'"


def needs_flag(suite: str, shard: str) -> str:
    """The exact NEEDS_<SUITE> value: true on the suite's own shard and on a manual run of the suite."""
    return (
        f"${{{{ (matrix.shard == '{shard}' || (matrix.shard == 'dispatch' && "
        f"github.event.inputs.connectors == '{suite}')) && 'true' || 'false' }}}}"
    )
_SHARD_LINE = re.compile(r'^\s*CONN_SHARD_(\d+):\s*"([^"]*)"', re.MULTILINE)


def _shard_of(text: str) -> dict[str, str]:
    """marker -> the connectors-N shard whose CONN_SHARD line lists it."""
    found = {}
    for number, value in _SHARD_LINE.findall(text):
        for name in value.split(" or "):
            if name.strip():
                found[name.strip()] = f"connectors-{number}"
    return found


def problems(text: str, tiers: dict) -> list[str]:
    found: list[str] = []
    try:
        job = yaml.safe_load(text)["jobs"][JOB]
    except (yaml.YAMLError, KeyError, TypeError) as exc:
        return [f"cannot read the {JOB} job of the workflow: {exc}"]
    steps = job.get("steps") or []
    job_env = job.get("env") or {}

    shard_of = _shard_of(text)
    for suite in tiers["suites"]:
        flag = f"NEEDS_{suite.upper()}"
        value = str(job_env.get(flag, ""))
        shard = shard_of.get(suite)
        if not value:
            found.append(f"{suite} has no {flag} in the job env, so nothing starts its simulator.")
            continue
        if shard and f"matrix.shard == '{shard}'" not in value:
            found.append(f"{flag} does not start the simulator on {shard}, the shard {suite} runs on.")
        if f"github.event.inputs.connectors == '{suite}'" not in value:
            found.append(f"{flag} does not start the simulator on a manual run of {suite}.")
        if shard and value != needs_flag(suite, shard):
            found.append(f"{flag} must be exactly `{needs_flag(suite, shard)}`: another form can start it everywhere "
                         f"but its shard, or nowhere.")

    runs = [str(step.get("run") or "") for step in steps]
    env_steps = [index for index, run in enumerate(runs) if "scripts/sim_shard_env.py" in run]
    checks = [index for index, run in enumerate(runs) if PROVIDER_CHECK_RUNS in run]
    if len(env_steps) != 1:
        found.append(f"expected one step running scripts/sim_shard_env.py, found {len(env_steps)}: simulator shards "
                     f"would run on the real model.")
    if len(checks) != 1:
        found.append(f"expected one step running {PROVIDER_CHECK_RUNS}, found {len(checks)}.")
    if len(env_steps) == 1:
        step = steps[env_steps[0]]
        if "if" in step:
            found.append("the step running scripts/sim_shard_env.py has an `if:`; it must run on every job.")
        extra = sorted(set(step) - {"name", "run", "if"})
        if extra:
            found.append(f"the step running scripts/sim_shard_env.py has {extra}: an `env:` can change the shard it "
                         f"reads, and continue-on-error lets a broken tiers file leave the shard on the real model.")
        if runs[env_steps[0]].strip() != ENV_STEP_RUNS:
            found.append(f"the step running scripts/sim_shard_env.py must be exactly `{ENV_STEP_RUNS}`.")
        if len(checks) == 1 and env_steps[0] > checks[0]:
            found.append("the step running scripts/sim_shard_env.py runs after the AI model check, which then checks "
                         "the real provider's settings.")

    for key in ("SIM_TIER", "SIM_SLOT"):
        if key in job_env:
            found.append(f"{key} is in the job env, where a simulator env file in GITHUB_ENV can override it.")
    for name in TEST_STEPS:
        step = next((step for step in steps if step.get("name") == name), None)
        if step is None:
            found.append(f"no '{name}' step.")
            continue
        env = step.get("env") or {}
        for key in ("SIM_TIER", "SIM_SLOT"):
            if key not in env:
                found.append(f"'{name}' does not set {key} at step level.")
        if env.get("SIM_SLOT") not in (None, SLOT_FROM_SETUP):
            found.append(f"'{name}' takes SIM_SLOT from elsewhere than the setup job, so a re-run may test other cases.")
        if env.get("SIM_TIER") not in (None, TIER_BY_EVENT):
            found.append(f"'{name}' sets SIM_TIER to something other than the tier each event uses: `{TIER_BY_EVENT}`.")
        lines = [line.strip() for line in (step.get("run") or "").splitlines() if not line.strip().startswith("#")]
        calls = [line for line in lines if _PYTEST_CALL.search(line)]
        if not calls:
            found.append(f"'{name}' runs no pytest.")
        for call in calls:
            if TIER_FLAGS not in call:
                found.append(f"'{name}' runs `{call[:60]}...` without {TIER_FLAGS}.")
            elif call.count("--sim-tier") != 1 or call.count("--sim-slot") != 1:
                found.append(f"'{name}' runs `{call[:60]}...` with a second --sim-tier or --sim-slot, which wins.")

    names = [step.get("name") for step in steps]
    leg_checks = [(index, step) for index, step in enumerate(steps) if HEALTH_CHECK in str(step.get("run") or "")]
    for test_name, stack in LEG_STACKS.items():
        leg = test_name.split(":", 1)[0]
        mine = [(index, step) for index, step in leg_checks if stack in str(step.get("run") or "")]
        if len(mine) != 1:
            found.append(f"expected a fake-model health check after each leg's tests, on that leg's stack ({stack}); "
                         f"the {leg} leg has {len(mine)}: a model that died mid-run would pass unseen.")
            continue
        index, step = mine[0]
        if test_name in names and index < names.index(test_name):
            found.append(f"'{step.get('name')}' runs before '{test_name}', so it cannot see a model that stops during "
                         f"the tests.")
        if FAILED_CALLS_CHECK not in str(step.get("run") or ""):
            found.append(f"'{step.get('name')}' does not fail on the fake model's failed calls (`{FAILED_CALLS_CHECK}` on "
                         f"its log): a model that stays up but fails calls would pass unseen.")
    for _, step in leg_checks:
        if "env.SIM_JOB == '1'" not in str(step.get("if", "")):
            found.append(f"'{step.get('name')}' does not run on simulator jobs (`env.SIM_JOB == '1'`).")

    setup_outputs = ((yaml.safe_load(text)["jobs"].get("setup") or {}).get("outputs") or {})
    if "sim_slot" not in setup_outputs:
        found.append("the setup job has no sim_slot output.")

    for number, line in enumerate(text.splitlines(), start=1):
        if "docker compose" in line and "_SIM_COMPOSE" in line and "$FAKE_MODEL_COMPOSE" not in line:
            found.append(f"line {number}: a docker compose line with a simulator's file but not $FAKE_MODEL_COMPOSE.")
    return found


def main() -> int:
    found = problems(WORKFLOW.read_text(encoding="utf-8"), json.loads(SIM_TIERS.read_text(encoding="utf-8")))
    for problem in found:
        print(f"PROBLEM: {problem}")
    if not found:
        print("sim wiring: ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
