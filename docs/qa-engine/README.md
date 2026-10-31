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

Before a managed stack starts, preflight checks the host ports its compose files publish (`docker compose config`, with every override applied), so a port another stack holds stops the run before the build. `--dry-run` runs the stack hooks too, so it lists every compose file and the image tag, but it builds and starts nothing. Two runs of the same commit share the image tag `pipeshub-ai:qa-<sha12>`: the second build replaces the first's tag, and the first run to finish removes it unless `QA_KEEP_IMAGE=1`. Run one build of a commit at a time. If the image build fails, every graph DB's stack tests are `error`, and the results carry no `environment` block. `results.json` records the image digest, the pinned base images and the lockfile hashes under `environment`; the report shows them, with one digest per stack job.

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
| `review --pr N \| --body-file F` | Trace the PR's stated requirements to this run's tests (`pass`, `fail`, `not_run`, `untested`), list risks and exploratory probes. Writes `review-input.json`, `review-llm.json`, `review.json` and `review.md`. `--run` executes the probes locally (they write under `review/` and never gate). `--comment` posts its own sticky comment, separate from the gate report. PR text is untrusted and sanitised |
| `bisect --range A..B --tests FILE` | Flake check, narrow, bisect to the breaking commit |
| `history record \| failing \| flaky` | Run history in a directory (`--store DIR`, default `$QA_HISTORY` or `qa-out/history`): `runs.jsonl` holds one compact entry per run (outcome counts and the failed, errored and flaky ids; the newest 1,000 from the last 90 days) and `tests.json` the last 10 outcomes per test. `record` adds the run in `--out`, with `--trigger` (default from `GITHUB_EVENT_NAME`) and `--wall-seconds`. `go` and `run` with `--history DIR` add their run too. `publish`, `pull` and `fetch-quarantine` work on the `qa-history` branch; see [History, quarantine and metrics](#history-quarantine-and-metrics) |
| `stuck --runs DIR...` | Print the tests that failed in each of the last 3 runs with distinct heads, oldest run directory first; exits 3 when there are any |
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
uses the new one. The stack matrix needs no such step: when the base engine
emits no `shards` output, it falls back to one shard, and with one shard the
run job passes no `--shard`, so a PR tree whose engine predates sharding still
runs. More than one shard needs a PR tree whose engine accepts `--shard`. The
`["neo4j"]` graph fallback applies only when the plan job exports no `graph_db`.

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
hides a fail on the other. Shard jobs' logs and JUnit files share names, so in
the report job's merged download one shard's can overwrite another's; each job's
own artifact keeps its full set.

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
recall of 98% or better; see [Enforcement](#enforcement).

**PR comment commands** (write access; `full` and `--trusted` need admin):

```text
/qa                      run the plan for this PR
/qa plan                 plan and report, run nothing
/qa missing              plan with missing-test analysis, runs nothing
/qa since <sha>          <sha> and every commit after it, as one batch (mode `since`)
/qa range <a>..<b>       an explicit range
/qa full                 every suite, both graph DBs (admin)
/qa run --profile conservative
/qa review                QA review comment: requirements traced to tests, risks, probes (write access, never gates)
/qa commit <sha>          run this PR's plan at an earlier commit of the PR (full sha)
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

## History, quarantine and metrics

### History

Run history lives on the orphan branch `qa-history`, as flat files only:

| File | Holds | Bound |
| --- | --- | --- |
| `runs.jsonl` | One compact entry per run: outcome counts and the failed, errored and flaky ids | Newest 1,000 from the last 90 days |
| `tests.json` | `{id: {"o": last 10 outcome letters, "seen": date}}`; `p` pass, `f` fail, `e` error, `k` flaky, newest last | Ids unseen for 90 days are dropped |
| `quarantine.json` | The automatic quarantine, `{"tests": [entry...]}` | Expired entries kept 30 days as a cooldown |
| `shadow.jsonl` | One shadow comparison per failed workflow run | 90 days |
| `metrics.json` | The latest metrics | Overwritten on every publish |

`qa history publish` adds the run in `--out` (`plan.json` and the merged
`results*.json`), any `--shadow FILE` lines, the quarantine update and fresh
metrics in one commit, `qa-history: <trigger> <edition> <head12>`. Every write
is a fast-forward. When another writer got there first, publish re-reads the
branch and re-applies its change, up to 5 times (printing
`qa-history: conflict, retrying (n/5)`), then exits 4. It never forces.

The files stay bounded, but every publish is a new commit, so the branch's git
history grows by one small commit per run. That is by design: the commits are
the audit record. `history pull` reads only the tip, and replaces the five
files in `--to`, removing any the branch no longer has.

**Who may write.** In GitHub Actions only a `schedule` run, or a `push` to the
default branch, may publish. Any other event (pull requests, dispatches, PR
comments) exits 2 and writes nothing. Outside Actions publishing is allowed;
without `--remote` it writes the local `qa-history` branch.

Run it locally:

```bash
python3 scripts/qa history publish --out qa-out     # local branch; flaky tests print "deferred (no --repo)"
git show qa-history:metrics.json
python3 scripts/qa history pull --to h               # copy the branch files into h/
python3 scripts/qa history flaky --store h
```

Options: `--remote NAME` publishes to (or pulls from) that remote's branch;
`--repo OWNER/NAME` opens flaky-test issues and reads bug metrics there;
`--started-at ISO` gives the wall time; `--run-url`; `--coverage SUITE=PATH`
(repeatable).

### Quarantine

A test flaky in at least 3 of its last 10 runs is quarantined for 14 days
(3 flakes in a shorter window count too). Publish opens or reuses the issue
`Flaky test: <id>` (label `qa-flaky`) before it writes, and an entry is never
added without one: if the issue cannot be made, the test waits for the next
publish. Ten straight passes release it early. It is not renewed: after it
expires the id cannot be re-added for 30 days, so only a human can make a
quarantine last, by adding it to `quarantine.toml` with an owner. Ids already
in `quarantine.toml` are left alone.

A quarantined test still runs, and the report lists it under "Quarantined (ran,
not blocking)" with its issue. Runs read the file through `QA_QUARANTINE_AUTO`;
`qa history fetch-quarantine --repo OWNER/NAME --to PATH` writes it from the
branch (`{"tests": []}` when there is none). If the fetch fails, the run has no
automatic entries, which only makes the gate stricter. A run accepts only entries
publish could have written: owner `qa-auto`, a GitHub issue URL, and an expiry at
most 14 days after `added` and at most 14 days from today. It ignores any other
entry with a warning, so editing the branch cannot quarantine a test for longer.
Issue titles longer than 256 characters are cut and end with a short hash of the
id; the body holds the full id.

### Metrics

`metrics.json` covers the last 30 days:

- `coverage`: tests per module and per flow from the catalog (a flow with none
  shows 0); line coverage per `--coverage` suite (`null` when the file is
  missing or unreadable); API operation hit rate, not measured yet.
- `trend`: runs and outcome counts per day and trigger (`nightly`, `main`,
  `dispatch`, `pr`, `local`).
- `flake`: flaky outcomes over all outcomes, overall and per suite, for tests
  seen in the window, and the quarantined ids.
- `duration`: p50 and p95 wall seconds per trigger (nearest rank). PR jobs do
  not write history, so `pr` comes from the completed runs of the `qa-pr*.yml`
  workflows (`gh run list`, start to last update).
- `bugs`: PRs the PR gate caught (a failed QA PR run before the last success)
  and issues labelled `escaped`, by `flow:<id>` label. Both stay 0 while the PR
  gate only plans. Without `--repo`, or on a `gh` error, it holds `error`.
- `enforcement`: the shadow verdict below.

Publish also writes a summary to `$GITHUB_STEP_SUMMARY` when it is set.

### Enforcement

Shadow recall compares the floor plan with the tests the existing workflows saw
fail (`qa shadow fetch`, `compare`). The nightly reads the failed runs of the
last 48 hours, so a run that finished after the previous nightly is not lost;
publish adds each run once. Runs whose head is in a fork are skipped. A
`pull_request` run is planned on the merge of its head into the base tip, as
GitHub tested it; a run whose head no longer merges cleanly is skipped with a
`merge conflict` line. A failure counts once per pull request (or branch),
however many pushes saw it. Each record lists at most 200 missed and unmapped
ids and keeps their full counts.

`qa shadow verdict` is ready only when the
oldest record is at least 14 days old, the last 14 days hold at least 20
distinct failures, and recall over them is at least 0.98. It exits 0 ready,
1 not ready, 2 not enough data.

The verdict covers only the `py` and `it` families that `unit-test.yml` and
`integration-tests.yml` report; it is not recall over every suite. To switch:

```bash
python3 scripts/qa history pull --to h && python3 scripts/qa shadow verdict --store h
```

If there is no `qa-history` branch yet, `history pull` says so, writes an empty
directory and exits 0, and `shadow verdict` then reports `insufficient` (exit 2).

If it exits 0, a maintainer sets the repository variable `QA_ENFORCE=true`.
Nothing sets it automatically.

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

## Claude Code skills

`.claude/skills/qa-pr`, `qa-commit` and `qa-review` walk an agent through
`go`, `go --commit` and `review`, next to `qa-planner`.

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
| `QA_COMPOSE_OVERRIDES` | unset | Extra compose files (`:`-separated, `{graph}` expands) applied over the CI stack |
| `QA_PORT_OFFSET` | unset | Moves every host port a managed stack publishes by this number, so it can run beside other stacks on a shared machine. The tests get the moved addresses from `integration-tests/stack/ports.py`; settings that point at another host are left alone. Values in `integration-tests/.env*` still win, because the tests load them over the environment |
| `QA_STACK_GRAPH_DB` | `TEST_GRAPH_DB_TYPE`, else the plan's first | Graph DB an external stack runs; the other DB's pass is `not_run` |
| `QA_IT_WORKERS` | unset | `pytest -n` for integration tests |
| `QA_MIN_DISK_GB` | 20 | Preflight before a managed stack |
| `QA_TIMEOUT_<FAMILY>` | 1–4 h | Per-runner timeout |
| `QA_OUT`, `QA_PROFILE`, `QA_EDITION` | `qa-out`, `standard`, `all` | Defaults for the CLI |
| `QA_HISTORY` | unset (`history` uses `qa-out/history`) | A history directory (`runs.jsonl`, `tests.json`, ...), for example one written by `qa history pull`; not a file |
| `QA_QUARANTINE_AUTO` | unset | A `quarantine.json` from `qa history fetch-quarantine`; its unexpired entries are quarantined for this run |

`QA_PLANNER_CMD` makes any agent CLI a planner. It gets `{prompt_file}`,
`{input}` and `{output}` placeholders, and its stdout or `{output}` must hold
the JSON reply. For example:
`QA_PLANNER_CMD='codex exec "$(cat {prompt_file})"'`.
