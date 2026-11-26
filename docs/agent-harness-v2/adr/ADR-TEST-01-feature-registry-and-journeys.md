# ADR-TEST-01: a feature registry, its coverage check, and journeys on the owner's shapes

Related: docs/test-plan/04-e2e-test-plan.md (the options matrix),
docs/test-plan/08-journeys.md (how to use it), ADR-OWD-07 D-LAZY-8 (the break that prompted it).

## Context

Every feature needs automated tests that catch breaks like these. Two breaks in two
days passed every test we had:

1. 2026-11-11, R26-LAZY: green on its own lane, then 5 of 62 smoke checks failed on the human-test stack (engine-job MCP, the budget-cut
   partial).
2. 2026-11-12: since R26-LAZY every engine or loop child dropped an MCP server its parent held whole
   (`child-runs.ts` read what a parent holds from `grants.tools`, which names none of a whole server's tools). A
   connected Atlassian Rovo server stopped working in Codex jobs. The human-test stack's own MongoDB shows it: 21 default-chat Codex children
   pinned with no MCP server at all (`assistant | child | engine | codex | mcpWhole=0 | mcpNamed=0`). Every unit test,
   the package lanes and the 65-check smoke passed.
3. The round-30 merge gate failed on cross-package interactions each package's tests passed (a test rig missing a
   method another package added; the tracker merge keeping duplicate rows).

### Why the options matrix did not catch break 2 (the audit)

The pairwise and three-way arrays (`e2e-factors.json`, 127 + 75 rows) were the closest thing to a combination design.
The cell that broke (an MCP server held whole x an engine child x the default assistant) was missed at every level:

- **The level was missing.** `tool_family` has one `mcp` level; nothing says how a run holds a server (named tools or
  whole at first use) or whose server it is (the person's own or an org's). There is no account-shape factor at all.
- **The combination was unrepresentable.** The matrix's routing input for `universal` (the default chat's Agent mode)
  omits the `harnessRequest: durable` the web app has sent since ASSIST1, so the oracle routes it legacy
  (`chat_mode_not_supported`, RV-70); and `engine_needs_durable_agent` admits an engine only on an agent mode. So the
  owner's main path, a durable default chat handing a job to Codex, could not be a row.
- **Rows that come close run on a fake that hides the grant path.** The fixture (`fixtures.py`) grants every MCP agent
  its tools by name (`mcpServers[].tools`), the one hold that worked. The R26-LAZY end-to-end tests (LZ-3, LZ-4,
  MCPUSE L2) ran their engines as saved agents whose grant names the tools, so a whole server never reached a child.
- **Rows that exist were never executed.** QA-FINAL runs only ST-1's 25 rows; the 4 engine x mcp pairwise rows and 7
  three-way rows are all on other stacks, which nothing runs on a schedule.
- **The smoke's shape was not the owner's.** Its seeded agents name their MCP tools explicitly; until R31-HOT its
  default-chat engine check ran only a shell command.

The root pattern: each test proves one feature on one path, chosen by whoever wrote it. Nothing enumerated features x
paths x the account shapes people have, so combinations went untested and only the owner found them.

## Decision

D1. **One registry** (`docs/test-plan/data/journeys.json`): every user-facing harness capability (23 features), every
path a run can take (21: classic, the default chat's Agent mode, a saved agent quick and deep, a loop sub-agent, an
engine job per release engine, schedule and webhook runs, resume after an approval or a question, recovery, the /mcp
and Slack surfaces, /run), and the account shapes that matter (4: the seeded saved agent, the owner's default chat, the
owner with their own engine pinned, a service account). A cell is feature x path x a shape the path admits. Rules mark a
cell impossible or out of scope, each with its reason (for example X-NAMED-OWNER: the default chat holds every server
whole, so "named tools" is not a cell of it).

D2. **Every feasible cell is accounted for.** `scripts/journey_matrix.py --check` fails unless each one is covered by
the journey runner's plan or by a listed real-stack test (pytest, Playwright, a smoke check or a lane script under
`integration-tests/harness/`, `deployment/e2e/` or `frontend/tests/e2e/`; a unit test never covers a cell), or is
matched by a gap that names its STATUS follow-up and what it waits on. A named test that does not exist, a matrix row
id that is no longer a row, a rule that decides no cell, a gap that matches nothing uncovered, a must-run cell the
runner does not run, or a stale render each fail the check. Gaps only shrink.

D3. **The code cannot grow a dimension behind the registry.** Cohesion test C-38 reads the code's own enumerations
(the tool sources `default_sources()` registers, the executor kinds of the run-spec schema, `RunKind`, the run spec's
`surface` and `via`, the release engine allowlist) and fails when one has a value the registry's `codeKeys` does not
map, or the map names one the code dropped.

D4. **Equivalence classes are explicit, not implied.** Where a feature cannot differ by path (erasure acts on stored
data, the same kinds per executor), a rule says which paths stand for the rest. Nothing is left out silently.

D5. **The owner's shape is a fixture, derived, not guessed.** `scripts/owner_shape.py --derive` reads a stack's MongoDB
read-only (find and count; no id, name, token, message or sealed field) and records run shapes as structure;
`deployment/e2e/owner_shape.json` holds the human-test stack's and how a test stack realises each shape (the owner's two servers held
whole, the org's or the person's coding engine per journey group, the org tool rule that asks). `--check` holds the
realisation to the derived counts.

D6. **Journeys prove behaviour.** `deployment/e2e/journeys.py` runs the plan on a human-style stack (run.sh human-up,
scripted model) through the web app's own routes. A line passes only on what the person and the outside world saw: the
tool's result in the person's stream, a card that held the call, the scripted SaaS's write recorded once or never (by
the arguments' digest), the run's journal ending, the child's engine and its tool environment, the pinned spec's
holds. The scripted model does not compose prose from tool results, so "the answer carried the result" is read as the
result reaching the person's stream, then the answer after it.

D7. **Two tiers.** The gate runs every feasible feature x path the runner implements once (on the path's primary shape,
then another shape for what the primary cannot carry) plus every must-run cell; the full tier runs every cell the runner
implements on every shape (paths x shapes is small enough to be exhaustive, so no covering array is needed). Journeys run
four at a time, grouped by the org's and the person's engine settings, which are shared state.

D8. **Every owner-reported break first becomes a failing journey** (a `mustRun` entry whose cell the runner runs; the
check refuses one it does not), before the fix lands. A finding the gate shows red is listed in `knownRed` with its
follow-up; the runner reports it as known and fails the day it passes, so the entry goes with the fix.

## As built

R31-JOURNEY (`harness/v2-r31journey`): `docs/test-plan/data/journeys.json` (+ `journeys.schema.json`), `scripts/journey_matrix.py` and `scripts/test_journey_matrix.py`, cohesion C-38 `test_c38_journey_registry.py`, `scripts/owner_shape.py` and `deployment/e2e/owner_shape.json`, `deployment/e2e/journeys.py`, `docs/test-plan/08-journeys.md`, the two `scripts/verify.sh` steps. R31-HOT2's carried pins are the feature `mcp.carried_pins` with must-run MR-6.

Consequences:


- The merge gate gains one lane step (the gate tier on the merged tip's human-style stack) and one standard-library
  check. Adding a capability means a registry entry, its rules, and a probe or a gap: the check says which.
- The options matrix stays for what it does well (stores, wires, deployment and policy factors); its factor file now has
  a known blind spot recorded here. Its `universal` routing input now carries the web app's `harness: durable`
  (R31JOURNEY-FU-7, R31-JFIX), held to the web app's request builder by `assist1-harness-request.test.ts`, so the
  default chat's Agent mode routes durably and its engine jobs are rows (PW-128..PW-131).
- The runner's literals (`SUPPORT`, `DRIVEN`, `TURNS`) are what the check reads; the runner refuses to start when they
  and its probes disagree, so the plan cannot claim a journey nothing runs.

R33-JGAPS (2026-11-13), gaps closed by user risk:

- **A path is a driver, a feature is a probe.** Every probe calls `run_turn`, which starts the run the way its path
  does: the chat stream; a schedule fired by Run now or by its own cron slot, or a signed webhook delivery
  (`start_scheduled`); PipesHub's own `/mcp` with a personal access token (`start_mcp`); a hold, then the worker and
  the query service killed and restarted (`recovered_turn`); a pause before the probe's own script on the resume paths
  (a card approved, a question answered). `/run` is one command per probe (`command_turn`). So a new probe runs on
  every path at once, and a new path every probe at once; no per-cell script.
- **Shared state is set for the whole run and put back.** The org's default web search and each seeded agent's are the
  scripted SearXNG (the stack's default was live DuckDuckGo), a Jira Data Center toolset on the scripted SaaS is
  signed in and granted, the org's memory policy is on, scheduled runs' Labs flag is on, a service-account agent is
  made, one personal access token is minted; attachment and memory words are canaries at the scripted model, set once
  (setting them replaces the set). A memory journey runs alone (every run of the person recalls kept memories); a
  recovery journey runs alone (it kills the processes every run uses).
- **The gate has a budget.** `tiers.gate.fullOnly` leaves named journeys to the full tier, each with its reason; the
  check refuses an entry with no reason or one that leaves out nothing, and a must-run's journey stays in the gate.
- **The account shape is honest about who owns the tools.** The `service` shape uses the saved agent's named server and
  rule (as `seeded`), not the person's servers held whole.

## Deviations

- "Pairwise across paths x shapes" in the brief: the full tier is exhaustive instead, because paths x shapes is 41
  pairs; a covering array would save nothing and hide cells.
- "The answer carried the result" is read as the result reaching the person's stream before the answer (D6): the
  scripted model cannot compose prose from a tool result.
- The "no second listing" proof of carried pins counts `tools/list` at the shared scripted SaaS, so that journey runs
  alone in its group.

## Owner amendments

None yet.

## Status

**Accepted** (R31-JOURNEY, 2026-11-12).
