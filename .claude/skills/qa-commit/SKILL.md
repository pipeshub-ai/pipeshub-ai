---
name: qa-commit
description: Test one exact commit in a detached worktree with a freshly built stack and prove which image ran. Use when asked to QA, verify or reproduce a failure at a specific commit sha with the QA engine (scripts/qa).
---

# QA one commit

1. Check `df -BG /` (at least 10 GB free) and `free -g` (at least 8 GB
   available).
2. Run `python3 scripts/qa go --commit <sha> --scope stack`. It tests that
   commit's own change in a detached worktree, builds the image and starts a
   managed stack.
3. Read `qa-out/report.md`, and `environment` in `qa-out/results.json`: it
   holds the image digest and the base image digests, which prove what ran.
   On a shared host, set `QA_PORT_OFFSET` (for example 20000) when ports are
   taken. Set `QA_IT_PYTHON` to a Python env that has the integration-tests
   deps, including `pytest_order` (`uv sync` in `integration-tests/`); the EE
   dev venv lacks `pytest_order`.
4. Remotely, comment `/qa commit <40-char sha>` on the PR (the full sha, a
   commit of that PR).

Never edit product code to make a test pass; report it.
