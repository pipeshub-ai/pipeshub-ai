# 08 Journeys: every feature on every path, on the shapes people have

Design: [ADR-TEST-01](../agent-harness-v2/adr/ADR-TEST-01-feature-registry-and-journeys.md). Generated coverage:
[generated/journeys.md](generated/journeys.md).

## The pieces

| What | Where | Run |
| --- | --- | --- |
| Feature registry (features, paths, shapes, rules, tests, gaps, must-runs, known reds, tiers) | `data/journeys.json` | edited by hand |
| Coverage check and plan | `scripts/journey_matrix.py` | `--check` (verify.sh, C-38), `--write`, `--stats` |
| The code's enumerations held to the registry | `backend/python/tests/cohesion/test_c38_journey_registry.py` | cohesion suite |
| Journey runner | `deployment/e2e/journeys.py` | `python3 deployment/e2e/journeys.py <run dir> --tier gate\|full` |
| Owner-shape fixture and its derivation | `deployment/e2e/owner_shape.json`, `scripts/owner_shape.py` | `--derive` (read-only), `--check` |

The runner needs a human-style stack (`run.sh human-up`, through `$PXH_LANE` when set, own run name and project
prefix) and the scripted model; it changes the org's default coding engine, the admin's own choice and, for an ACP
engine, the org's engine ceiling, and puts each back after its group.

## Rules

1. **Every owner-reported break first becomes a failing journey.** Before the fix: a `mustRun` entry naming the cell
   (feature, path, shape) the break lives in, with the evidence; if the runner does not run that cell yet, extend its
   `SUPPORT`/`DRIVEN` (or add a probe) until it does — `journey_matrix.py --check` refuses a must-run the runner does not
   run. The journey is red on the base, green with the fix.
2. **A new capability, path or executor is a registry entry first.** C-38 fails when the code gains a tool source,
   executor kind, run kind, surface, via or release engine the registry does not map. Then its cells: covered by a
   probe, a listed real-stack test, or a gap with its follow-up row.
3. **A cell is covered only end to end.** A unit test never covers a cell. A listed test must start a run on that path
   and assert the feature's behaviour; a matrix row is listed only while QA-FINAL runs its stack.
4. **A known red is temporary.** A gate line red for a product finding goes in `knownRed` with its follow-up; the
   runner fails the day it passes, so the entry leaves with the fix.
5. **The merge gate runs the gate tier** on each round's merged tip, on its own human-style stack, after the smoke
   (`deployment/e2e/smoke_human.py`); the full tier runs nightly.

## Adding a probe

A probe is one `TURNS` entry (the features one conversation proves) and its function in `PROBES`; `SUPPORT` says on
which paths it runs and `DRIVEN` which shapes each path's driver sets up. Then `python3 scripts/journey_matrix.py
--write`, and drop the gap entries that now match nothing (the check says which).

## Closing the gaps by user risk (R33-JGAPS)

The order the gaps were closed in, highest risk first. Risk is what people use, weighted by the owner's shape
(`deployment/e2e/owner_shape.json`, derived from the human-test stack: default-chat Agent-mode runs and their Codex jobs, most with web
search granted and both of the person's servers held whole; saved agents with their opencode, Codex and Claude Code
jobs; one org tool rule that asks; no schedule, service account or Slack run), then by what has broken before (the
must-runs MR-1..8 and the manual-test reports: MCP on jobs, a budget cut's partial, a job's repository, answers that do
not say what they could not do).

Cells closed per batch, measured (a cell can count in two batches; 280 in all, 365 covered before, 645 after).

| Rank | Batch | Why this rank | Probe or driver | Cells |
| --- | --- | --- | --- | --- |
| 1 | Web search on every chat and job path | Granted on most of the owner's default-chat runs and their Codex jobs; the stack's default provider was live DuckDuckGo, so no test could call it | `web` turn; the run sets the org's default provider and each seeded agent's to the scripted SearXNG (`scripted_web_search`) | 30 |
| 2 | A run that stops for the person and goes on (`resume.approval`, `resume.question`) | The owner's breaks were all on runs that pause: MCP pins, approvals and skills after a card or a question | every probe through `run_turn`: the pause first (a card approved, a question answered), then the probe's own script | 24 |
| 3 | Attachments | People attach files to the default chat and expect the job it hands off to read them | `attach` turn: an upload, its words a canary at the scripted model; on a job, the job reads the file back | 22 |
| 4 | The flow guard | A private read followed by an external write is the data-leak path; nothing held it end to end on most paths | `flow` turn: the MCP note, then the write; the card names `flow/flow_external_write`; denied, nothing written | 27 |
| 5 | Questions and skill loads on jobs; out of memory | "Jobs must ask back" (answer contract on every path); skills on jobs (MR-2); an OOM end named (R31GVISO-FU-3) | `question` with the gateway's `ask_user`; the engine's skill tool; `oom` turn (engine sandbox, and `run_code` on loop paths) | 77 |
| 6 | Toolsets and memory | Connector tools and kept memories on the default chat and jobs | `toolset` turn (Jira Data Center on the scripted SaaS); `memory` turn (kept, then given to a new conversation's model) | 39 |
| 7 | Paths the owner does not use yet but the product offers | Schedules, signed webhooks, `/mcp`, `/run`, crash recovery, a service-account agent | drivers in `run_turn` (`start_scheduled`, `start_mcp`, `recovered_turn`, `command_turn`, `service_agent`) | 99 |
| 8 | Left as gaps, each with its row | No scripted Slack workspace; the MCP hub's stack; the answer contract while R33-WORDS changes it; erasure as a journey; a pinned engine's parent turn | gaps R31JOURNEY-FU-2, -4, -5, -6, R33JGAPS-FU-4 | 197 |

Every line is proved by behaviour (D6): the tool offered and called (the scripted SaaS, SearXNG or Jira saw it), the
card that held a call and its rule, a write done once or never (by its arguments' digest), the result in the person's
stream, the run's ending in its journal. A cell's journey that is red for a product reason is a `knownRed` entry with
its follow-up (R33JGAPS-FU-1..3).

## Tiers and their budget

The gate runs every feasible feature x path the runner implements once, plus every must-run cell, except what
`tiers.gate.fullOnly` leaves to the full tier: each entry names the journeys (by turn, path, shape) and why (slow and
redundant with a gate journey of the same probe on another path). `journey_matrix.py --check` refuses an entry with no
reason or one that leaves out no journey; a must-run cell's journey stays in the gate whatever an entry says. A
recovery journey kills the worker and the query service, so it runs alone; `run.sh human-up` supervises the query
service for it (`E2E_SUPERVISE_QUERY=1`).
