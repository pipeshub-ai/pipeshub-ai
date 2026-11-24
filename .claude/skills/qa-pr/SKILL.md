---
name: qa-pr
description: QA the current branch or a pull request - plan the tests the change needs, run them and read the report. Use when asked to test, QA or verify a PR, a branch or the working tree with the QA engine (scripts/qa).
---

# QA a branch or PR

1. Plan only, to see what the change needs:
   `python3 scripts/qa go --adapter none --plan-only`, then read
   `qa-out/report.md`.
2. Execute the plan: `python3 scripts/qa test` (the same as `go`; `test --full`
   runs the full profile). When the plan selects stack
   tests, check `df -BG /` (at least 10 GB free) and `free -g` (at least 8 GB
   available) first, then add `--stack managed --build`.
3. Read `qa-out/report.md`. Failures and `not_run` rows are the result: quote
   them verbatim.
4. To strengthen the change's tests: `python3 scripts/qa test --improve` finds the
   tests that still pass with the change broken and adds proven stronger and
   missing tests as new `test_<name>_qa.py` files, uncommitted. It runs
   model-written tests on this machine: only in a checkout whose own tests you
   would run. Read `git diff` before you commit any of them.
5. Remotely, comment `/qa` or `/qa run` on the PR (write access): it runs the
   plan, then (with `QA_ENFORCE` on) on a PR from this repository pushes proven
   improvements to the branch as one commit, unless the PR is approved. `/qa full` runs every suite and does not improve.

Never edit product code to make a test pass; report it.
