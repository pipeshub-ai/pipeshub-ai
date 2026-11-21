---
name: qa-planner
description: Act as the QA planner for a change in this repo - decide which extra tests (beyond the deterministic floor) a PR or commit range needs, which backends to run, and which tests are missing. Use when asked to plan QA for a PR/branch/commit range, or when the QA engine (scripts/qa) hands you a plan-input.json.
---

# QA planner

The planning instructions are model-agnostic and live in
`scripts/qa/prompts/planner.md`. Read that file and follow it exactly; this
skill only adds how to get the inputs when nobody has prepared them.

## Inputs already prepared (CI, or `python3 scripts/qa plan`)

Read the `plan-input.json` and `changeset.diff` paths you were given and reply
with the JSON object the prompt specifies. Nothing else.

## Planning interactively

1. Build the floor for the change:
   - a PR branch: `python3 scripts/qa go --adapter none --plan-only`
   - "this commit and everything after": `python3 scripts/qa go --since <sha> --adapter none --plan-only`
   - uncommitted work: add `--worktree`
2. Read `qa-out/plan-input.json` and `qa-out/changeset.diff`, then follow
   `scripts/qa/prompts/planner.md` to decide additions.
3. Write your JSON to `qa-out/plan-llm.json` with
   `"_planner": {"adapter": "claude_code", "status": "ok"}` added, then run
   `python3 scripts/qa validate` and `python3 scripts/qa report` to see the
   merged plan. To execute it: `python3 scripts/qa run` (add `--stack external`
   with `PIPESHUB_BASE_URL` for integration tests against a running stack).

Never remove tests from the floor and never invent catalog ids: grep the
catalogs listed in `plan-input.json` → `catalogs`.
