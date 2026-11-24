---
name: qa-generate
description: Generate planned test scenarios for a module, then, after a human approves them, implement and prove the tests. Use when asked to write new tests for an area with the QA engine (scripts/qa).
---

# QA generate

**Warning.** `qa generate --implement` runs model-written test code on your machine, under your account, during `qa prove`, before you review it. The checks on the reply are a best-effort filter, not a sandbox, and a determined or injected reply can bypass them. Run it only for a PR whose scenarios you have reviewed and labelled `qa-scenarios-ok`, in a checkout and on a machine where you would run that PR's own tests. Never run it with production credentials in your environment. Read `git diff` on every kept test before you commit it. In CI, `/qa tests --implement` runs model-written tests once the PR has the label, in a job with no secrets and no token, and pushes the proven ones to the PR branch; review them there before you merge.

The `qa-scenarios-ok` label is a human process control. It is not bound to the stub, the head commit or the repo.

From a PR, comment `/qa tests <id>` for steps 1-2 and, after the label, `/qa tests --implement <ids>` for step 4: each pushes one commit to the PR branch (same-repo PRs, write access). The short local forms are `python3 scripts/qa tests <id>` and `python3 scripts/qa tests --implement <ids> --pr <n>`.

1. Check `df -BG /` and `free -g`. Run `python3 scripts/qa generate --module <id>`
   (or `--from-plan` after `python3 scripts/qa go --adapter none --plan-only`).
   Read `qa-out/generate.md`.
2. Commit the stubs and open a PR.
3. Stop. Ask the human to review the scenarios and add the label
   `qa-scenarios-ok`. Never add the label yourself.
4. `python3 scripts/qa generate --implement <ids> --pr <n>`. Add `--base <ref>`
   to prove against the PR's change, and `--stack managed` for integration rows. Kept tests stay uncommitted.
5. Read `qa-out/prove/*.json` and paste each verdict into the PR body. Show the
   human `git diff` of every kept test. Do not commit them for the human.
6. `python3 scripts/qa prove <id>` re-checks one test.

Never edit product code to make a test pass; a test that fails on head is a bug report, not a fix.
