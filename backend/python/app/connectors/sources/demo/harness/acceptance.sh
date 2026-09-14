#!/usr/bin/env bash
# Acceptance test for the Acme Corp demo data.
#
# Against an instance where the Demo connector has synced and the two sample
# employees exist (bootstrap-first-run.sh with PIPESHUB_DEMO_DATA=1 and
# PIPESHUB_DEMO_PERSONAS=1), ask each golden question RUNS times as Alice and
# as Bob through the conversation API and score the citations.
#
# Pass: every question passes at least MIN_PASS of RUNS per persona, and the
# restricted pricing question passes every run — Bob must always get the
# document, Alice must never see it. A single leak fails the whole run.
#
# Usage:
#   DEMO_PERSONA_PASSWORD='...' ./acceptance.sh path/to/bootstrap.env [RUNS] [MIN_PASS]
set -euo pipefail

ENV_FILE="${1:?usage: acceptance.sh ENV_FILE [RUNS] [MIN_PASS]}"
RUNS="${2:-10}"
MIN_PASS="${3:-9}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="$HERE/../fixture/acme-corp.yaml"

: "${DEMO_PERSONA_PASSWORD:?set DEMO_PERSONA_PASSWORD to the password given to the personas (PIPESHUB_DEMO_PASSWORD at first run)}"

status=0
for persona in alice bob; do
  echo "=================================================================="
  echo " persona: $persona   runs: $RUNS   min pass: $MIN_PASS"
  echo "=================================================================="
  if ! uv run --with pipeshub-sdk --with httpx --with pyyaml \
      python "$HERE/kb_harness.py" --env "$ENV_FILE" --fixture "$FIXTURE" \
      --persona "$persona" --runs "$RUNS" --min-pass "$MIN_PASS"; then
    status=1
  fi
done

if [[ $status -eq 0 ]]; then
  echo "ACCEPTANCE PASSED: both personas"
else
  echo "ACCEPTANCE FAILED: see the summaries above"
fi
exit $status
