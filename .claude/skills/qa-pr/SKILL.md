---
name: qa-pr
description: QA the current branch or a pull request - plan the tests the change needs, run them and read the report. Use when asked to test, QA or verify a PR, a branch or the working tree with the QA engine (scripts/qa).
---

# QA a branch or PR

1. Plan only, to see what the change needs:
   `python3 scripts/qa go --adapter none --plan-only`, then read
   `qa-out/report.md`.
2. Execute the plan: `python3 scripts/qa go`. When the plan selects stack
   tests, check `df -BG /` (at least 10 GB free) and `free -g` (at least 8 GB
   available) first, then add `--stack managed --build`.
3. Read `qa-out/report.md`. Failures and `not_run` rows are the result: quote
   them verbatim.
4. Remotely, comment `/qa run` on the PR (write access).

Never edit product code to make a test pass; report it.
