# QA engine

The QA engine decides which tests a change needs, runs them on the existing
test setup, and reports on the PR with one gate check. A deterministic floor
(path rules, `covers:` tags, mirror tests, import graphs, always-run rules)
decides what must run. A model, Claude by default but any model through an
adapter, can only add to that floor. A model failure means the floor alone
runs.

- Design and decisions: *PipesHub AI QA Engine — Design Doc*
- Build plan: *PipesHub AI QA Engine — Implementation Plan*
- Contracts, algorithms and invariants: [spec.md](spec.md)

The engine is stdlib-only Python 3.12 in `scripts/qa/`, run as
`python3 scripts/qa <command>`. It works the same on a laptop and in CI.

## Quick start

```bash
# What would QA run for my branch? (no model, nothing executed)
python3 scripts/qa go --adapter none --plan-only
cat qa-out/report.md

# This commit and everything after it, as one batch
python3 scripts/qa go --since <sha> --adapter none --plan-only

# Uncommitted work too
python3 scripts/qa go --worktree --adapter none --plan-only

# Print the exact commands the plan would run
python3 scripts/qa run --dry-run

# Run it: unit suites locally, integration against a stack you already have up
QA_PYTHON=backend/python/venv/bin/python \
PIPESHUB_BASE_URL=http://localhost:3000 \
PIPESHUB_TEST_USER_EMAIL=... PIPESHUB_TEST_USER_PASSWORD=... \
python3 scripts/qa run --stack external
python3 scripts/qa report --gate

# Test one exact commit: a throwaway worktree at <sha>, a managed stack built from it
python3 scripts/qa go --commit <sha> --scope stack --graph-db neo4j

# Ask Claude for additions (needs the claude CLI and a key)
python3 scripts/qa go --adapter claude_code --plan-only
```

`--commit` installs nothing into its worktree: Python suites use `QA_PYTHON` / `QA_IT_PYTHON`, and Node suites report `not_run` (kind `toolchain`), which blocks the gate. Use it with `--scope stack`.

A managed stack generates `PIPESHUB_TEST_USER_PASSWORD` for each run and replaces any value in the environment.

Everything lands in `qa-out/` (git-ignored). Use `--out DIR` for a different run
directory.

## Commands

| Command | What it does |
| --- | --- |
| `catalog build \| check \| lint \| show` | Build the committed integration catalogs; check they are current; lint specs (TC ids, `covers:`, rule paths, quarantine expiry); print every catalog row |
| `collect` | What changed: `--since SHA`, `--range A..B`, `--base REF`, or the PR's merge-base (default). `--worktree` includes uncommitted files |
| `impact` | The floor: `--profile fast\|standard\|conservative\|full`, `--edition all` or an edition the rules define |
| `plan --adapter none\|claude_code\|command\|litellm` | Ask a model for additions; always writes `plan-llm.json`, with a status |
| `validate` | Merge floor and additions into `plan.json` |
| `run` | Execute `plan.json`, or `--tests FILE` / `--ids ...`. `--stack none\|managed\|external`, `--scope unit\|stack`, `--dry-run` |
| `report` | Render `report.md`; `--summary`, `--comment --pr N`, `--gate`. `--allow-not-run KINDS` lists the `not_run` kinds that do not block (default `profile,scope,secret`); `--strict` or `--allow-not-run ""` makes every `not_run` block |
| `go` | collect → impact → plan → validate → run → report; takes `--gate`, `--allow-not-run` and `--strict` like `report`. `--commit SHA` runs it in a detached worktree at SHA with `--build`, and defaults to a managed stack unless `--stack` is given. For a commit already on the base branch it tests that commit's own change (a root commit runs the full profile). It cannot be combined with `--head`; results land in your `--out` |
| `bisect --range A..B --tests FILE` | Flake check, narrow, bisect to the breaking commit |
| `history record \| failing \| flaky` | Run history |
| `command --body ... --permission ...` | Parse a `/qa` PR comment |
| `score --adapter X` | Score an adapter on past changes with known failures |

## In CI

| Workflow | Runs | Purpose |
| --- | --- | --- |
| `qa-pr.yml` | PRs | Catalog, rule floor only (no secrets, no model); check *QA-PR / pipeline / gate* |
| `qa.yml` | `/qa` dispatches, nightly | Catalog with the model and secrets; check *QA / pipeline / gate* |
| `qa-pipeline.yml` | called by the callers above | plan → run-unit → run-stack → report → gate |
| `qa-command.yml` | `/qa` PR comments | permission check, then dispatch at the PR head |
| `main.yml` | every PR | engine unit tests, catalog freshness, spec lint |

**Engine version.** The plan, report and gate jobs run the base branch's engine
against the PR's tree. A change to the engine's CLI flags or artifact schema
rolls out in two steps: the engine first accepts both forms, then the workflow
uses the new one.

The engine comes from the base branch, but the `pull_request` workflow file
comes from the PR, so a PR can edit the workflow to skip the gate. The gate is
only base-controlled while `.github/workflows/qa*.yml`, `scripts/qa/**` and
rule overlays need an owner's review (CODEOWNERS plus branch protection
that requires code-owner review).

**Stack shards.** `run-stack` is a matrix of graph DB × shard. The plan job
sets the shard cap from the `QA_STACK_SHARDS` repository variable (default 1 on
pull requests, 4 otherwise) and uses `min(cap, groups)` shards. A group is one
connector marker with measured minutes in `scripts/shard_durations.json`, else
one test file, so a connector suite or an ordered module never splits; a group
with no measured minutes counts as 5. Groups go to the lightest shard, longest
first (`scripts/qa/shard.py`). Each job runs `qa run --graph-db G --shard K/N`,
writes `results-stack-G-KofN.json`, and lists the other shards' stack tests as
`not_run` `scope`. The report merges the parts: an executed outcome beats
`not_run`, and the worst executed outcome wins, so a pass on one graph DB never
hides a fail on the other.

The run jobs call `scripts/qa` from the PR's tree. Once `QA_ENFORCE` is on, a PR
branch created before the engine landed has no `scripts/qa` and must be rebased.

**Not run.** Every planned test ends with an outcome; one that did not run is
`not_run` with a kind: `secret` (a credential is missing), `scope` (outside the
job's `--scope`, or needs a stack or graph DB the run does not have), `profile` (deferred by
the profile), `infra`, `toolchain`, `cancelled` or `no_result` (no job reported
it). The PR comment and the gate read the same list from `QA_ALLOW_NOT_RUN` in
`qa-pipeline.yml`: `secret,profile` on PRs and dispatches, so `scope` blocks, and
none on the nightly schedule or a `full` run.

**Secrets.** A `pull_request` job runs the PR's own code, so the PR workflows
pass no secrets (`scripts/test_workflow_trust.py` enforces this). The model
planner and the stack run only from `qa.yml`, dispatched by `/qa`
or the nightly schedule.

**Shadow mode.** Until the repository variable `QA_ENFORCE` is `true`, only
`plan` and `report` run. The PR gets the plan as a comment, and the existing
workflows keep running everything. Turn enforcement on once shadow data shows
recall of 98% or better (design doc, rollout P1–P3).

**PR comment commands** (write access; `full` and `--trusted` need admin):

```text
/qa                      run the plan for this PR
/qa plan                 plan and report, run nothing
/qa missing              plan with missing-test analysis, runs nothing
/qa since <sha>          <sha> and every commit after it, as one batch (mode `since`)
/qa range <a>..<b>       an explicit range
/qa full                 every suite, both graph DBs (admin)
/qa run --profile conservative
/qa run --trusted <sha>  fork PRs only: run with secrets at the reviewed head; <sha> must be the full 40 characters, refused if the PR moved (admin)
```

**Labels:** `qa-full` and `qa-conservative` raise the profile. Draft PRs use
`fast`.

**Repository variables:** `QA_ENFORCE`, `QA_PLANNER` (adapter, default
`claude_code`), `QA_PLANNER_MODEL`, `QA_PLANNER_CMD`, `AZURE_API_VERSION`,
`QA_ADMIN_TEAM` (team slug allowed to run `/qa full`).

**Choosing the model.** The planner is not tied to one provider. Without a
usable configuration the run uses the floor alone, so a missing key never blocks
a PR.

| Provider | `QA_PLANNER` | `QA_PLANNER_MODEL` | Secrets |
| --- | --- | --- | --- |
| Claude (Claude Code, reads the repo itself) | `claude_code` | optional | `ANTHROPIC_API_KEY` |
| Azure OpenAI | `litellm` | `azure/<deployment>` | `AZURE_API_KEY`, `AZURE_API_BASE` (+ variable `AZURE_API_VERSION`) |
| OpenAI | `litellm` | `openai/<model>` | `OPENAI_API_KEY` |
| Gemini | `litellm` | `gemini/<model>` | `GEMINI_API_KEY` |
| Claude via the API | `litellm` | `anthropic/<model>` | `ANTHROPIC_API_KEY` |
| Any agent CLI (Codex, Gemini CLI, ...) | `command` | - | `QA_PLANNER_CMD`; install the CLI in the plan job |

`claude_code` and agent CLIs can grep the catalogs and read code. `litellm` sends
one request with `plan-input.json` and the diff, so it can add only tests listed
in `candidates`, which are ranked by how closely they relate to the change.

## Writing tests the engine understands

Integration tests (and any test you want the planner to reason about) declare
their spec in the first docstring line:

```python
async def test_tc_jira_004_issue_properties(...):
    """TC-JIRA-004: every synced issue has key, status, assignee and project in the graph.

    covers: backend/python/app/connectors/sources/atlassian/jira*/
    needs: env:JIRA_TEST_API_TOKEN, service:graph-db
    """
```

- `TC-<AREA>-<NNN>` must be unique within its directory. Connectors reuse
  scenario ids such as `TC-SYNC-001` across directories on purpose.
- `covers:` lists source globs this test protects. A change to any of them
  selects the test.
- `needs:` lists `env:NAME`, `service:NAME` or `secret:NAME`. Credentials named
  in a conftest's `source_unavailable(..., secrets=[...])` are picked up
  automatically. So is the AI model key of any test that reaches a fixture
  calling a helper listed in the suite's `fixture_needs` (for example
  `setup_test_llm_model`), whether it requests the fixture directly, through
  another fixture, or via an autouse fixture. In `env:A|B+C`, A alone or B and
  C together satisfy the need. A test whose need is unmet is reported
  `not_run` with the missing names, not run and failed.
- A test you want but have not written yet is a stub with
  `@pytest.mark.planned`. It is listed in the catalog as a known gap and
  always skipped.

After changing tests, run `python3 scripts/qa catalog build` and commit the
catalog. CI fails when it is stale.

## Teaching the engine about a new area

1. **New suite:** add a `[[suite]]` to `scripts/qa/rules/suites.toml` (an overlay adds its own in
   `scripts/<name>/qa/rules/suites.toml`).
2. **Cross-process coupling** the import graph cannot see (an HTTP route, a
   broker topic, a graph query): add a `[[rule]]` to `impact.toml`, or add a
   `covers:` line to the integration test.
3. **A file whose change should rerun a whole suite** (lockfiles, configs,
   shared fixtures): add it to `[[full_run]]` in `always.toml`.
4. Run `python3 scripts/qa catalog lint`. It warns when a rule's paths match no
   file.

Every nightly miss opens a `qa-miss` issue with the test that should have run.
Each one usually becomes one of the edits above.

## Environment variables

| Variable | Default | Used for |
| --- | --- | --- |
| `QA_PYTHON` | current interpreter | Backend pytest suites (needs backend deps + pytest) |
| `QA_IT_PYTHON` | `integration-tests/.venv/bin/python` | Integration tests |
| `QA_NPX` | `npx` | mocha, vitest, Playwright |
| `QA_PLANNER`, `QA_PLANNER_MODEL`, `QA_PLANNER_CMD`, `QA_PLANNER_MAX_TURNS` | `claude_code` | Model adapter and settings |
| `QA_STACK`, `PIPESHUB_BASE_URL`, `QA_COMPOSE_PROJECT` | `none`, `http://localhost:3000`, `qa-<sha12>-<run>` | Integration stack. Each managed project is `<prefix>-<graph>`, and the image is `pipeshub-ai:qa-<sha12>` |
| `QA_RUN_ID` | `GITHUB_RUN_ID`, else the process id | The `<run>` in managed compose project names, so concurrent runs of one commit never share a stack |
| `QA_KEEP_IMAGE` | unset | `1` keeps the image a `--build` run made; otherwise it is removed after the last stack is torn down |
| `QA_COMPOSE_OVERRIDES` | unset | Extra compose files (`:`-separated, `{graph}` expands) applied over the CI stack, e.g. to remap host ports on a shared machine |
| `QA_STACK_GRAPH_DB` | `TEST_GRAPH_DB_TYPE`, else the plan's first | Graph DB an external stack runs; the other DB's pass is `not_run` |
| `QA_IT_WORKERS` | unset | `pytest -n` for integration tests |
| `QA_MIN_DISK_GB` | 20 | Preflight before a managed stack |
| `QA_TIMEOUT_<FAMILY>` | 1–4 h | Per-runner timeout |
| `QA_OUT`, `QA_PROFILE`, `QA_EDITION`, `QA_HISTORY` | `qa-out`, `standard`, `all`, unset | Defaults for the CLI |

`QA_PLANNER_CMD` makes any agent CLI a planner. It gets `{prompt_file}`,
`{input}` and `{output}` placeholders, and its stdout or `{output}` must hold
the JSON reply. For example:
`QA_PLANNER_CMD='codex exec "$(cat {prompt_file})"'`.
