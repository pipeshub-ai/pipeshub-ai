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
# Short forms: test = go for this branch (standard profile), test --full = the full profile,
# tests <module> = generate --module, tests --implement <ids> --pr N = generate --implement
python3 scripts/qa test
python3 scripts/qa test --full
# Then find the change's weak tests and add proven stronger and missing ones, uncommitted (needs a model)
python3 scripts/qa test --improve

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

# The QA agent: Claude Code picks the tests, the engine runs them, Claude judges and adds tests
# (your terminal's claude login is used; --stack none runs the unit suites only)
python3 scripts/qa agent --stack managed --graph-db neo4j --gate
# Stack pull requests on main first, then test the result
python3 scripts/qa agent --combine 12:<sha> --combine 15:<sha> --combine-base origin/main
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
| `go` (alias `test`) | `--full` is `--profile full` (the two cannot be combined): every suite, on your checkout. `/qa full` is different: CI's full mode, which also covers both graph DBs. collect → impact → plan → validate → run → report; takes `--gate`, `--allow-not-run` and `--strict` like `report`. `--commit SHA` runs it in a detached worktree at SHA with `--build`, and defaults to a managed stack unless `--stack` is given. For a commit already on the base branch it tests that commit's own change (a root commit runs the full profile). It cannot be combined with `--head`; results land in your `--out` |
| `review --pr N \| --body-file F` | Trace the PR's stated requirements to this run's tests (`pass`, `fail`, `not_run`, `untested`), list risks and exploratory probes. Writes `review-input.json`, `review-llm.json`, `review.json` and `review.md`. `--run` executes the probes locally (they write under `review/` and never gate). `--comment` posts its own comment, separate from the gate report (see [PR comments](#pr-comments)). PR text is untrusted and sanitised |
| `generate --module ID [--from-plan]` (alias `tests ID`) | Ask a model for planned scenarios and write each as a stub test in a `planned = true` suite; writes `scenarios-llm.json`, `generate.md`, `generate.json` (the written and dropped stubs). Never overwrites a file |
| `generate --implement ID[,ID...] --pr N` (alias `tests --implement ...`) | Replace committed, clean stubs with model-written tests and keep only the proven ones, uncommitted. Needs the label `qa-scenarios-ok` on PR N. Takes `--base`, `--repeat`, `--max-mutants`, `--stack`, `--base-url` for `prove`. A stack row is refused unless `--stack` gives a stack. `--draft` asks the model and checks the replies but writes and runs nothing; `--from-drafts` then proves those replies with no model call and no label call. CI uses the pair so the job that runs the generated code holds no model key |
| `improve` (or `go --improve`) | After a run, for the unit tests that cover a changed Python file (at most `--max-checked`, default 20): run each against the reverted change and up to `--check-mutants` (default 3) mutants of its changed lines. A test that passes every variant is weak. Then ask the model for a stronger companion of each weak test and up to `--max-new` (default 10) tests for missing scenarios of the changed files, each a new file next to the existing tests (`test_<name>_qa.py`), never an edit of one. Each reply passes the same checks as `generate --implement`, and only proven files stay, uncommitted. `--check`, `--draft` and `--from-drafts` run one phase each (CI runs them in separate jobs); `--minutes` (default 10) stops each phase from starting new work. The flags read `QA_IMPROVE_MAX_CHECKED`, `QA_IMPROVE_MUTANTS`, `QA_IMPROVE_MAX_NEW` and `QA_IMPROVE_MINUTES`. A model failure or timeout is reported as skipped. Writes `improve-check.json`, `improve.json` and `improve.md` |
| `agent` | The QA agent (see [QA agent](#qa-agent)): round 0 runs the floor, then Claude Code picks, runs (through the engine) and judges further rounds and writes tests. `--stack managed\|none`, `--minutes` (default 120), `--max-turns` (default 150), `--combine NUMBER:SHA` (repeat, in order) with `--combine-base REF`, `--gate`, `--comment --pr N` |
| `stack up \| down \| status` | One managed stack for several runs: `up [--plan F] [--graph-db G] [--no-build]` starts and seeds it and writes `stack/session.json` (owner-only; it holds the stack's admin password); `run --session FILE` runs any tests against it; `down` collects its logs and removes it with its image |
| `prove ID` | Run one test on head `--repeat` times (default 3, 1 for stack tests), then with the change reverted (`--base REF`) or up to `--max-mutants` mutants (default 5, 1 for stack tests). A failing variant counts only if it fails again on a rerun; writes `prove/<id>.json` |
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
| `qa-tests.yml` | `/qa tests` dispatches, and qa.yml's improve job for `/qa` | `check` (improve only: no secrets, no token, runs the PR's tests against mutants), `draft` (model key, runs no generated code), `prove` (no secrets, no token), `publish` (the only job with `contents: write`): pushes one signed-off commit to a same-repo PR branch and comments |
| `qa-agent-publish.yml` | called by `qa.yml` after a QA agent run | no secrets: pushes the agent's test files to a same-repo PR as one signed-off commit, or for `/qa combine --open-pr` the combined branch and a draft PR |
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

**Stack variants.** A test that needs `stack:<name>` runs in its own stack job.
`run-stack` adds the plan's `variants` (`""` is the default stack) to its matrix
and passes `--stack-variant NAME` for a non-empty one. The variant job writes
`results-stack-G-KofN-NAME.json` and lists every other stack test as `not_run`
`scope`; the report's merge keeps the executed outcome. Variants are declared
in `[variants.<name>]` in `rules/always.toml`.

The run jobs call `scripts/qa` from the PR's tree. Once `QA_ENFORCE` is on, a PR
branch created before the engine landed has no `scripts/qa` and must be rebased.

**Not run.** Every planned test ends with an outcome; one that did not run is
`not_run` with a kind: `secret` (a credential is missing), `scope` (outside the
job's `--scope`, or needs a stack or graph DB the run does not have), `profile` (deferred by
the profile), `infra`, `toolchain`, `cancelled` or `no_result` (no job reported
it). In CI a test one run job leaves to another (the unit job lists the stack tests as `scope`)
is reported `cancelled` when that other job was cancelled, and `no_result` naming the job when it
did not report the test; both block. The PR comment and the gate read the same list from `QA_ALLOW_NOT_RUN` in
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

**Reading the report.** Failures are grouped by suite family, largest group first, with a count per
group. Each group lists at most 25, fewer when the report would not fit in a PR comment, and always ends
with "… and N more in <group> (see results.json)". The Quarantined list and the Not run table are capped the same way, and a comment still over 65,000 characters is cut with a visible note. A "By module" table sits next to "By flow", and
`catalog lint` warns with the number of stack tests that belong to no flow.

**What a suite runs.** A mocha suite runs the files its own mocharc selects (`spec` minus `ignore`, plus
the suite's `exclude`), the same files as its npm script. The engine reads a mocharc strictly (`key: value`
or a block list, and only `*`, `**` and `?` in globs): a form it cannot read stops `catalog check`, `catalog lint`
and the run with an error naming the file. `catalog lint` fails when a spec matches none of the suite's files and
warns with the count of files the mocharc leaves out; those files belong to no suite and are not orphans. A planned
file with no case in its runner's JUnit output is an `error`, even when the runner exited 0. The `scripts` suite is one `unittest` run: a failure counts against the file its
report names, and what no selected file explains is one `scripts:(suite)` result with the real count. A
pytest run that writes no JUnit file names the first error line of its log in each test's reason.

**Integration tests run per conftest scope.** Pytest imports every `conftest.py` named on one command line as the
module `conftest`, so a stack run is one process per directory that owns a `conftest.py` (else per top-level
directory), with its own JUnit file. A group that crashes marks only its own tests. Each group gets
`QA_TIMEOUT_IT_PYTEST` (default 90 minutes), or the time left before the run's deadline if that is less. Nothing
starts after `QA_DEADLINE_SECONDS` (default 270 minutes, inside the `run-stack` job's 300): those tests are `not_run` kind
`deadline`, which blocks. A test id pytest cannot find is an `error` for that id alone: the group runs again without it.
On a managed stack with several groups the engine sets `PIPESHUB_TEST_LEAVE_SEEDED_MODELS=1`, so the AI models one
session seeds stay for the next instead of being removed while its records still index.

**PR comments.** The automatic per-push run (`qa-pr.yml`) keeps one comment per
edition and edits it on every push. Every `/qa` command run (`/qa`, `/qa full`,
`/qa plan`, `/qa missing`, `/qa since`, `/qa range`, `/qa commit`, the
`/qa review` comment and `/qa tests`) posts a new comment instead, so two runs
are never confused. Its header names the command, who asked (without a
mention), the head commit tested and the run, and it carries a marker with the
run id (`<!-- qa-run:<id> -->`). The improve stage of `/qa` adds its section to
that run's comment. After posting, the bot collapses its older comments of the
same kind on the PR from other `/qa` runs as outdated (GraphQL
`minimizeComment`, which needs `pull-requests: write`); a failure there is only
a warning. Only comments by `github-actions[bot]` are ever edited or collapsed.

**PR comment commands** (write access; `--trusted` and `full --profile conservative` need admin):

```text
/qa                      run the plan for this PR, then improve its tests (same-repo PRs; see below); the QA agent when QA_AGENT is true
/qa agent                the QA agent: Claude Code picks and judges the tests, the engine runs them (see QA agent)
/qa combine #12 #15 [--open-pr]  stack these PRs on the default branch, resolve conflicts, run the QA agent on the result
/qa plan                 plan and report, run nothing
/qa missing              plan with missing-test analysis, runs nothing
/qa since <sha>          <sha> and every commit after it, as one batch (mode `since`)
/qa range <a>..<b>       an explicit range
/qa full                 every suite, both graph DBs
/qa run --profile conservative
/qa review                QA review comment: requirements traced to tests, risks, probes (write access, never gates)
/qa commit <sha>          run this PR's plan at an earlier commit of the PR (full sha)
/qa tests <module>       propose missing scenarios for a module and push the stubs to the PR branch
/qa tests --implement <ID[,ID...]>  needs the label qa-scenarios-ok: implement, prove, push only the proven tests
/qa run --trusted <sha>  fork PRs only: run with secrets at the reviewed head; <sha> must be the full 40 characters, refused if the PR moved (admin)
```

**What `/qa` does.** It runs the plan (the rule floor plus the model's
additions), and on a PR from this repository qa.yml then runs the improve stage
through `qa-tests.yml`, with no label and no approval: weak tests of the change
are found, and proven stronger and missing tests are pushed to the PR branch as
one commit (see [Improving tests after /qa](#improving-tests-after-qa)). The
report comment gains an "Improvements" section. The gate is about the test run
only: improvements never make it pass or fail. `/qa plan`, `/qa missing`,
`/qa review`, `/qa full`, `/qa since`, `/qa range`, `/qa commit` and the nightly
run have no improve stage. On a fork PR the stage is skipped and the PR is told so. The stage runs only
with `QA_ENFORCE` on, and the repository variable `QA_IMPROVE=false` turns it off.

**Labels:** `qa-full` and `qa-conservative` raise the profile. Draft PRs use
`fast`.

**Repository variables:** `QA_ENFORCE`, `QA_PLANNER` (adapter, default
`claude_code`), `QA_PLANNER_MODEL`, `QA_PLANNER_CMD`, `AZURE_API_VERSION`,
`QA_ADMIN_TEAM` (team slug allowed to use `--trusted` and `/qa full --profile conservative`),
`QA_IMPROVE` (`false` turns off the improve stage of `/qa`),
`QA_STACK_RUNNER` (a GitHub-hosted runner label such as `ubuntu-latest` for the `run-stack` job,
for a repository without the RunsOn runners; the job then frees disk space before it builds the
stack. Unset, `run-stack` uses the RunsOn runner).

**Choosing the model.** The planner is not tied to one provider. Without a
usable configuration the run uses the floor alone, so a missing key never blocks
a PR.

| Provider | `QA_PLANNER` | `QA_PLANNER_MODEL` | Secrets |
| --- | --- | --- | --- |
| Claude (Claude Code, reads the repo itself) | `claude_code` | optional | `ANTHROPIC_API_KEY`, or `CLAUDE_CODE_OAUTH_TOKEN` to use a Claude subscription login instead (create it with `claude setup-token`) |
| Azure OpenAI | `litellm` | `azure/<deployment>` | `AZURE_API_KEY`, `AZURE_API_BASE` (+ variable `AZURE_API_VERSION`) |
| OpenAI | `litellm` | `openai/<model>` | `OPENAI_API_KEY` |
| Gemini | `litellm` | `gemini/<model>` | `GEMINI_API_KEY` |
| Claude via the API | `litellm` | `anthropic/<model>` | `ANTHROPIC_API_KEY` |
| Any agent CLI (Codex, Gemini CLI, ...) | `command` | - | `QA_PLANNER_CMD`; install the CLI in the plan job |

`claude_code` can search the catalogs and read code with the Read, Glob and Grep
tools only, and reads only the tree and the run directory (never `/proc` or
`/sys`, where the environment and so the model key can be read); it cannot run
commands or write files, and it loads only your user settings
(`--setting-sources user`) and no MCP servers (`--strict-mcp-config`), never the
`.claude/` settings or `.mcp.json` of the tree it plans. A `command` CLI gets whatever tools you give it in `QA_PLANNER_CMD`. `litellm` sends
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

### PR stacks use a stand-in model

A pull request run has no model key. Its stack seeds the `web-fixtures`
stand-in as the org's indexing models: the workflow sets
`TEST_AI_MODEL_PROVIDER=standIn` and `TEST_STAND_IN_MODEL_ENDPOINT`, and
`integration-tests/helper/ai_models_setup.py` adds both models as
`openAICompatible` against it. The stand-in answers "OK" to every chat prompt
and returns hash-derived embedding vectors, so indexing finishes but search
ranking and chat answers mean nothing. Mark a test that asserts on either with
`@pytest.mark.live_model`: it keeps the real-key need and is `not_run` on a PR.
`integration-tests/unit/test_live_model_marks.py` fails when an unmarked test
reads model output. Real keys with no forced provider never land on the stand-in.

To use it locally, export the same two variables and point the endpoint at the
stand-in as the stack reaches it. A test script can set replies and tool calls
per prompt with `PUT /__fixtures__/openai/script` (see
`integration-tests/web_fixtures/server.py`).

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

## Generating tests

1. `python3 scripts/qa generate --module <id>` (add `--from-plan` after `go --plan-only`). It writes stubs and `qa-out/generate.md`. Commit the stubs and open a PR.
2. A person reviews the scenarios and adds the label `qa-scenarios-ok`.
3. `python3 scripts/qa generate --implement <ids> --pr <n>` asks the model for each test file, checks it, proves it and leaves only proven tests uncommitted. Evidence is in `qa-out/prove/*.json`. `python3 scripts/qa prove <id>` re-checks one test.

From a PR, `/qa tests <module>` runs step 1 and pushes the stubs to the PR branch, and `/qa tests --implement <ids>` runs step 3 and pushes only the proven tests (see [/qa tests in CI](#qa-tests-in-ci)).

### /qa tests in CI

`qa-command.yml` checks the commenter's permission (write) and the arguments (a module id, or at most ten catalog ids), then dispatches `qa-tests.yml` at the PR head it read. That workflow:

1. **draft** checks the arguments again and that the PR head is still that commit. A fork PR runs only when `qa-command.yml` dispatched it (an admin's `--trusted <sha>`): the triggering actor of that dispatch is `github-actions[bot]`, which a person's direct dispatch never is. It then runs the default branch's engine on the PR tree with the model key and without a GitHub token: `--implement` reads the label `qa-scenarios-ok` from the API at that moment through a token file the engine deletes before the model starts, refuses ids without it, and only asks the model and checks each reply (`--draft`). `<module>` writes the stubs. The model reads only the tree and the run directory, with no MCP servers. No generated code runs here. If any provider key or the token appears in the output, nothing is uploaded; otherwise the job reports the artifact id and a SHA-256 of its files.
2. **prove** (`--implement` only) runs `--from-drafts --stack none`: it writes each checked reply and proves it, with no secrets, no token and read-only permissions. It runs the PR's own code, so publish trusts nothing it produces except its list of kept ids, and only for ids the draft drafted. Stack-grain rows are refused with a reason: this job has no stack, and building one (the image, the stand-in model, compose) is the `run-stack` job's half hour, not a budget for a comment command. Run those locally with `python3 scripts/qa tests --implement <ids> --pr N --stack managed`.
3. **publish**, the only job with `contents: write`, downloads the draft by its id and checks its hash, then re-reads the PR. It pushes only if the PR is open, from this repository, still at the dispatched commit, not approved or set to auto-merge, and its branch is not the default branch, `qa-history` or a protected branch: it copies the stub files the engine recorded, or, for `--implement`, the draft's checked content of each id the prove job kept, rebuilds the catalog, and pushes one commit signed off by `github-actions[bot]` that names the command and who asked. The token is handed to that push alone, and a change that holds it is not pushed. The push is never forced, so a branch that moved during the run rejects it. It always comments with the draft's summary and the ids kept and not kept ("proven" means proven by the PR's own test environment); the proof JSON is in the `qa-tests-proof` artifact. A push made with the workflow token does not start other workflows, so the comment asks you to push or re-run the checks.

A fork PR is refused: the workflow cannot push to a fork, and fork code does not run with secrets until an admin reviewed it. With `--trusted <sha>` an admin runs it at that commit, and the files stay in the run's artifacts.

**Warning.** `qa generate --implement` runs model-written test code on your machine, under your account, during `qa prove`, before you review it. The checks on the reply are a best-effort filter, not a sandbox, and a determined or injected reply can bypass them. Run it only for a PR whose scenarios you have reviewed and labelled `qa-scenarios-ok`, in a checkout and on a machine where you would run that PR's own tests. Never run it with production credentials in your environment. Read `git diff` on every kept test before you commit it. In CI, `/qa tests --implement` runs model-written tests once the PR has the label: in the `prove` job, which has no secrets and no token, and the proven tests are then pushed to the PR branch, where the PR's own checks run them like any other change. Review them there before you merge.

The `qa-scenarios-ok` label is a human process control. It is not bound to the stub, the head commit or the repo.

### Improving tests after /qa

`/qa` on a PR from this repository sets `improve` on qa.yml, whose `improve` job calls `qa-tests.yml` after the test run. It runs only while the run executes tests (`QA_ENFORCE` is `true`); in shadow mode there is no improve stage. Set the repository variable `QA_IMPROVE` to `false` to turn it off. It needs no label: reviewers judge the pushed tests in normal PR review. The jobs keep every rule of `/qa tests --implement`:

1. **guard** (a read token, runs nothing of the PR) checks that the PR is open, from this repository and still at the dispatched commit, before any of its code runs.
2. **check** (no secrets, no token, read-only) runs `improve --check` on the PR head: the unit tests that cover a changed Python file are run against the reverted change and mutants of the changed lines. It runs the PR's code, so its weak list is untrusted: draft keeps only ids of the candidate list it computes itself, and only mutants on their covered files with the engine's operators.
3. **draft** (no GitHub token in the model step, and only the selected adapter's key) refuses a fork PR and a PR that moved, then runs `improve --draft`: the model gets the diff, the changed files, their modules, the candidate tests and the weak list, and returns stronger companions and tests for missing scenarios. The engine reads only regular files inside the tree for it, never a symlink, a directory or a device. The engine picks every path (a new `test_<name>_qa.py` next to the existing test), never rewrites a test a person wrote, and checks each reply like `--implement` does. Integration-level drafts are reported as needing a stack and not written. Output holding a secret is not uploaded.
4. **prove** (no secrets, no token) writes each draft and keeps it only if it passes on head every time and fails, again on a rerun, on the reverted change or a mutant.
5. **publish** works as for `/qa tests --implement`: it verifies the draft artifact by id and hash, takes the content from the draft only, keeps only drafted ids that prove kept, writes only new files, refuses a fork, a moved PR, the default branch, `qa-history` and protected branches, and pushes one signed-off commit without force. It does not push to a PR that is approved or set to auto-merge: the comment lists the proven files, which stay in the `qa-tests-draft` artifact. It then puts the Improvements section (weak tests with the mutants they survived, the drafted companions and missing tests, the dropped drafts and why, and the files kept) into this run's `/qa` report comment (the bot's comment with the run's `<!-- qa-run:<id> -->` marker), between `<!-- qa-improve:start -->` and `<!-- qa-improve:end -->`, or posts it alone when that comment is missing or full.

A model failure or timeout is reported as skipped and fails nothing. Budgets: 20 tests checked with 3 mutants each, 10 new tests, and each engine phase (check, prove) starts no new work after 10 minutes, with the model's 15-minute timeout between them, about 45 minutes in all (`QA_IMPROVE_*`, see [Commands](#commands)). `check` writes its results after each test, so a job that times out keeps what finished. The diff is taken against the default branch. Locally, `python3 scripts/qa test --improve` runs the same loop after the tests and leaves the proven files uncommitted.

**Warning.** On this path, model-written test code runs with no human approval, but only in the `check` and `prove` jobs, which hold no secrets and no token. A push made with the workflow token starts no workflow, so the PR's checks run the pushed files only after the next push or a re-run of the checks: push a commit or re-run the checks, and review the files before you merge. Locally, `test --improve` runs that code on your machine, under your account: run it only in a checkout where you would run the PR's own tests, with no production credentials in your environment.

**Threat model note.** `check` and `prove` run the PR's code in a dispatched run on the default branch. Code there can reach the job's Actions runtime token and write cache entries scoped to the default branch, as the run jobs of qa-pipeline.yml already can. Workflows that release or deploy from the default branch should not restore caches.

## QA agent

The QA agent works like a QA engineer, with the engine as its test runner. Claude Code reads the change and the code around it, decides which tests to run and what they should show, has the engine run them, reads the results and logs, and repeats: it reruns what looks flaky, runs related tests the rules did not pick, and writes and proves tests for behaviour nothing covers. It ends with a verdict. Claude never runs a test runner itself, and its verdict never gates: the gate reads the engine's results only.

**How a run goes** (`qa agent`, or `/qa agent` in CI):

1. The engine computes the rule floor, starts one managed stack (`qa stack up`), and runs the floor as round 0.
2. `claude -p` starts with `scripts/qa/prompts/agent.md` and an input file: the change and its diff, the floor and round 0's results, the catalog, the stack, the exact commands it may use and its time budget. Its tools: read and search anything; edit and write files; Bash only for `python3 <engine> run --session ...`, `catalog`, `prove` and `stack status`, and read-only git. Project settings, hooks and MCP servers of the tree are not loaded.
3. Each further round runs through the engine into `agent/rounds/<n>/`. The results of every round merge into `results-agent.json`: the latest result counts, a test that failed and then passed is `flaky`, and a later `not_run` hides nothing. Tests outside the floor join the plan with `source: agent`.
4. Claude writes `agent/verdict.json` (`qa-agent/v1`): `works`, `broken` or `unsure`, a summary, the rounds and their reasons, and findings, each a `regression`, `existing-bug`, `flaky`, `environment`, `test-bug` or `combined-only` with its tests and evidence. Unknown test ids and malformed fields are dropped and listed.
5. Changes outside test files are reverted and listed; test changes go to `agent-tests.patch` (locally they stay in your checkout, uncommitted). Output holding a secret value drops the verdict and the tests.
6. The stack is removed, also on an error or a cancel, and the report gains a **QA agent** section.

Without the `claude` CLI, or in CI without `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, the floor alone runs and the report says so.

**In CI.** `/qa agent` runs it. Set the repository variable `QA_AGENT` to `true` and plain `/qa` runs it too. Claude Code signs in with the secret `CLAUDE_CODE_OAUTH_TOKEN` (a Claude subscription login, made with `claude setup-token`) or `ANTHROPIC_API_KEY`; only the agent step gets it. The pipeline skips `run-unit` and runs one stack job (`QA_STACK_RUNNER`, or the default stack runner) that installs the unit-test toolchains and the Claude Code CLI, takes the engine and the prompt from the default branch, and runs `qa agent`. `QA_AGENT_MINUTES` sets its budget. After the report, `qa-agent-publish.yml`, which holds no secrets, pushes the agent's test files to a same-repo PR with the same checks as the improve stage: the PR is open, unmoved, not approved or set to auto-merge, its branch not protected; only test files the default branch's engine recognises; no secret; one signed-off commit, never forced. It comments what it did.

**Combined runs.** `/qa combine #12 #15 #18` (2 to 8 open pull requests from this repository, in stacking order) tests several PRs together. Each PR's head is pinned when the command runs, and the run stops if one moved. `qa agent --combine` stacks them on the default branch, one commit per PR, so there are no merge commits. When a PR conflicts with the ones before it, its commit keeps the combined side, and Claude Code (`scripts/qa/prompts/combine.md`) resolves the conflicting hunks in a commit of its own, `combine: resolve #15 against #12 in <files>`. The build stops and says why when markers remain, when the resolver touched any other file, or when a conflict is a delete against a change or a binary file. The agent then runs on the combined tree, and a failure that only two PRs together cause is `combined-only`, naming both. The report lists the PRs and each resolved conflict. With `--open-pr`, the publish job pushes the combined commits (and the agent's tests) to a new branch `qa-combined/<run id>`, refusing files none of the PRs touches, opens a draft PR, and comments on every combined PR. Nothing is pushed to the PRs themselves, and nothing is merged.

**Warning.** The agent job runs the PR's code while it holds Claude's credential and the stack's test settings, like the stack job holds the test secrets. A PR author with write access could make a test print or send them. Run it on PRs from this repository only; on a fork PR only through an admin's `--trusted <sha>`.

## Claude Code skills

`.claude/skills/qa-pr`, `qa-commit`, `qa-review` and `qa-generate` walk an agent through
`go`, `go --commit`, `review` and `generate`/`prove`, next to `qa-planner`.

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

A local `--stack managed` run needs the same non-secret settings the CI `run-stack` job sets in `.github/workflows/qa-pipeline.yml` (its `env:` block). At least `PIPESHUB_TEST_ENV=local`, or the integration tests stop before the first test; tests that read mail also need `SMTP_HOST` and `SMTP_PORT`. Secrets stay unset unless you mean to use them.

| Variable | Default | Used for |
| --- | --- | --- |
| `QA_PYTHON` | current interpreter | Backend pytest suites (needs backend deps + pytest) |
| `QA_IT_PYTHON` | `integration-tests/.venv/bin/python` | Integration tests |
| `QA_NPX` | `npx` | mocha, vitest, Playwright |
| `QA_PLAYWRIGHT_ARGS` | none | Extra Playwright arguments, split on spaces. The nightly passes `--update-snapshots=missing` |
| `QA_PLANNER`, `QA_PLANNER_MODEL`, `QA_PLANNER_CMD`, `QA_PLANNER_MAX_TURNS` | `claude_code` | Model adapter and settings |
| `QA_DEADLINE_SECONDS`, `QA_TIMEOUT_IT_PYTEST` | `16200`, `5400` | Run-level deadline; timeout of one integration-test group |
| `QA_AGENT_MINUTES`, `QA_AGENT_MAX_TURNS` | `120`, `150` | The QA agent's time and turn budget |
| `QA_IMPROVE_MAX_CHECKED`, `QA_IMPROVE_MUTANTS`, `QA_IMPROVE_MAX_NEW`, `QA_IMPROVE_MINUTES` | `20`, `3`, `10`, `10` | `improve` budgets: tests checked, mutants per test, new tests, minutes per phase |
| `TEST_USER_EMAIL`, `TEST_USER_PASSWORD`, `BASE_URL`, `NEXT_PUBLIC_API_BASE_URL` | the stack's `PIPESHUB_TEST_USER_EMAIL`, `PIPESHUB_TEST_USER_PASSWORD` and `PIPESHUB_BASE_URL` | What the Playwright specs read; set only for Playwright and only when unset. When a setup or provisioning spec (`*.setup.ts`, `*.provision.ts`) fails, the specs of that run that Playwright skipped are reported `error` ("setup failed: ..."), not `skip` |
| `QA_STACK`, `PIPESHUB_BASE_URL`, `QA_COMPOSE_PROJECT` | `none`, `http://localhost:3000`, `qa-<sha12>-<run>` | Integration stack. Each managed project is `<prefix>-<graph>`, and the image is `pipeshub-ai:qa-<sha12>` |
| `QA_RUN_ID` | `GITHUB_RUN_ID`, else the process id | The `<run>` in managed compose project names, so concurrent runs of one commit never share a stack |
| `QA_KEEP_IMAGE` | unset | `1` keeps the image a `--build` run made; otherwise it is removed after the last stack is torn down |
| `QA_COMPOSE_OVERRIDES` | unset | Extra compose files (`:`-separated, `{graph}` expands) applied over the CI stack |
| `QA_PORT_OFFSET` | unset | Moves every host port a managed stack publishes by this number, so it can run beside other stacks on a shared machine. The tests get the moved addresses from `integration-tests/stack/ports.py`; settings that point at another host are left alone. Values in `integration-tests/.env*` still win, because the tests load them over the environment |
| `QA_STACK_VARIANT` | unset (the default stack) | The stack variant to run (`--stack-variant`): only tests that need `stack:<name>` run, the rest are `not_run` `scope`. Overlay hooks read it |
| `PERF_SMOKE_LABEL` | `smoke-local-neo4j` | The perf smoke's baseline file, `integration-tests/perf/baselines/smoke-<label>.json` |
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

## Nightly visual diff

The `e2e-visual` suite (`profiles = ["full"]`) runs the responsive screenshot spec on the stack of a full run; no other profile selects it. The suite's `env` sets `E2E_VISUAL=true` on its Playwright run, so the `visual` project exists. On the nightly schedule the `run-stack` job first downloads the newest unexpired `visual-baseline-<edition>` artifact from `main` into `$RUNNER_TEMP/visual-baseline` (looked up by artifact name, so the Sunday weekly run, which uploads none, never breaks the chain; `actions: read`; a missing artifact is not an error), sets `VISUAL_BASELINE_DIR` to it and passes `--update-snapshots=missing` through `QA_PLAYWRIGHT_ARGS`. Screenshots with no baseline are written and pass; the others are compared. Then each `-actual.png` (a screenshot that differed) replaces its baseline PNG, so the folder is tonight's screenshots, and the one job that ran the suite on neo4j uploads it as `visual-baseline-<edition>` (14 days). An intentional UI change is therefore red for one night, and the next night compares against it. `-diff.png` images go into the stack results artifact. Without `VISUAL_BASELINE_DIR` the screenshots are only saved, so a full run on a pull request never fails on them.

## Weekly

The `weekly` profile runs only the suites whose `profiles` include it (today `it-weekly`: the upgrade smoke and the perf smoke). No other profile selects them, and a model planner cannot add them either. It runs on the Sunday cron of `qa.yml` (04:00 UTC), and on demand with `/qa run --profile weekly` (write permission) or the `weekly` choice of a workflow dispatch.

- **Upgrade smoke** (stack variant `upgrade`): boots the previous release (`git describe --tags --abbrev=0 --match 'v*' HEAD^`), runs the `upgrade_before` tests to seed and snapshot, switches the app to this commit's image on the same volumes, boots it twice, then compares. It needs the tags and the history, so in CI the upgrade job checks out with full history; locally run it from a clone that has the tags.
- **Perf smoke** (stack variant `perf`): one small run of the indexing and query benchmarks against the stand-in model. It fails above twice the baseline in `integration-tests/perf/baselines/smoke-<PERF_SMOKE_LABEL>.json`, on any error, and on an operation that produced no measurement. With no baseline file it judges only the last two. Its numbers are in `integration-tests/reports/perf/smoke/summary.json`, which the CI results artifact keeps, so a CI baseline is that file committed under the CI label.

Locally, one run per variant: `python3 scripts/qa go --profile weekly --stack managed --build --stack-variant upgrade`, then the same with `--stack-variant perf`.
