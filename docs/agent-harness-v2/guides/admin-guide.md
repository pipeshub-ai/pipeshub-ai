# Agent harness: admin guide

Checklist item C18.03. This page is for operators who install PipesHub and for org admins who run it. It covers
turning the harness and coding engines on, allowed websites and git hosts, budgets, kill switches, approvals,
audit and run settings. Labels in **bold** are the English labels in the app
(`frontend/lib/i18n/locales/en-US.json`). Every switch is listed in full in [FLAGS.md](../FLAGS.md), whose
machine-readable twin is `contracts/harness/flags.json`; the switch table below is checked against that file by
`backend/python/tests/cohesion/test_v2_guides.py`.

The agent harness is in beta and off by default for people: turn on the server setting (`AGENT_HARNESS_WORKER_ENABLED`,
on by default), then turn on **Agent harness (Beta)** in Labs, which is off by default. The app marks its pages and
Labs switches **Beta**.

Two kinds of people change these settings:

- **The operator** sets environment variables on the deployment (Docker Compose `.env`, Helm values). Org admins
  cannot change them.
- **An org admin** uses the pages under Workspace settings. An admin can only make an org stricter than the
  deployment allows, never looser.

## Turning the harness on

Durable runs need all of the following. The first one that is not met is the reason the app reports
(`features[durable_chat].order` in `contracts/harness/flags.json`); **Services** (`/workspace/services`) shows
whether durable agent runs are on, and why not.

1. **Infrastructure (operator).** A MongoDB replica set with a `majority` journal, plus the relay and the worker
   processes (`AGENT_HARNESS_WORKER_ENABLED`, on by default). Docker Compose ships all three on. Helm's own `values.yaml` answers `arbiter`, so durable chat stays
   off there; the `values-local`, `values-cloud` and `values-eks` files answer `majority` (FLAGS.md, "Where the
   journal is durable").
2. **The instance kill switch is off (operator).** See [Kill switches](#kill-switches).
3. **Labs (org admin).** Workspace → **Labs** (`/workspace/labs`) → **Agent harness (Beta)**. It is off by default,
   so an admin turns it on; until then the workspace shows admins a notice with a link to Labs. It is proven by `backend/nodejs/apps/tests/modules/configuration_manager/ctl5-labs-three-flags.test.ts`.
4. **Model families and chat mode.** The org must have a model the harness supports, and the request's chat mode
   must allow it (FLAGS.md).

Coding engines need one more operator switch, `HARNESS_ENGINE_RUNS=true`, which is off by default. On Compose,
set it in `.env` and restart the app container; the Helm chart does not set it. They also need `SANDBOX_MODE=broker`, the shipped default
(`backend/python/app/utils/env_flags.py`).

### Switches

The security profile, `PIPESHUB_HARDENED` (ADR-OPS-07), is independent of these: unset or false keeps the previous
release's behaviour on upgrade, true runs the hardened defaults; `install.sh` writes true on a fresh install
(guides/HARNESS-OFF.md, "Security profile").

| Switch | Kind | Default | What it does |
|---|---|---|---|
| `REPLICA_SET_AVAILABLE` | env | false | Says the MongoDB is a replica set; durable runs need it. Compose sets it to true |
| `AGENT_HARNESS_RELAY_ENABLED` | env | true | Starts the relay in Node |
| `AGENT_HARNESS_WORKER_ENABLED` | env | true | The agent harness's server setting (ADR-OPS-06, amended 2026-11-20): starts the worker and its gateways, idle until an org turns on Labs **Agent harness**, and serves every harness route, Labs flag and screen; false, the install runs as a release without the harness (guides/HARNESS-OFF.md) |
| `HARNESS_ENGINE_RUNS` | env | false | Lets the worker run coding engines |
| `AGENT_HARNESS_KILL_SWITCH` | env | false | Sends every new run to the classic chat |
| `ENABLE_AGENT_HARNESS` | labs | false | The org's **Agent harness (Beta)** switch |
| `ENABLE_SCHEDULED_RUNS` | labs | false | Scheduled and triggered runs |
| `ENABLE_KNOWLEDGE_PUBLICATIONS` | labs | false | Knowledge publications |
| `HARNESS_FLOW_GUARD` | env | true | The flow guard (Rule of Two), see [Approvals](#approvals) |
| `SANDBOX_ALLOW_NETWORK` | env | false | Open network for legacy `run_code` sandboxes |
| `EGRESS_CONNECT_TUNNELS` | env | false | Host-level HTTPS tunnels through the egress proxy |
| `AGENT_HARNESS_BUDGET_LEDGER` | env | true | Meters spend against budgets |

### gVisor

The release requires gVisor (`runsc`) for coding engines: `engineProfile.isolationMin` is `gvisor` in
`contracts/harness/core/policy/release.json`. Without `runsc` an engine run is refused (`gvisor_unavailable`) and
admins see "Coding engines need the gVisor sandbox runtime on this server". `run_code` and every other feature keep
working. `./install.sh --install-gvisor` installs it ([GVISOR.md](../../../deployment/docker-compose/GVISOR.md)).

For development and tests only, `PIPESHUB_ENGINES_WITHOUT_GVISOR=true` (exactly `true`) runs engines on hardened
runc, and admins see "Coding engines run without gVisor — weaker isolation". An agent whose engine profile asks
for gVisor is still refused. Proven by `backend/python/tests/unit/harness_core/engines/test_gvisor_floor.py` and
`backend/nodejs/apps/tests/modules/agent-harness/gvisor-floor.test.ts` (GV-N1, GV-N4).

**Not yet tested on gVisor.** No test host has `runsc`, so every `[gvisor]` test is skipped and the isolation and
egress suites ran on runc only. gVisor and Kata testing is deferred to the last step of the project (the project tracker rows
C2.38, C6.05, C20.39). Helm has no agent-sandbox or Kata support yet and turns engines off
(`ENGINE_SANDBOX_MISSING`, pinned by
`backend/python/tests/unit/harness/recs_audit/test_r2_r6_isolation_and_protocols.py::test_r2_helm_has_no_agent_sandbox_or_kata_yet`).

### Which engines and how they run

Every engine run gets this release's engine profile (`engineProfile` in `contracts/harness/core/policy/release.json`).
An agent's own engine profile can only narrow it (`ENGINE_PROFILE_WIDENING`), and so can a request
(`backend/nodejs/apps/tests/modules/agent-harness/org-policy-defaults.test.ts`). Every member may use every configured
model and pick their own coding engine under **My AI accounts**, add their own keys and sign in their own Claude account.

The released engines are listed in `backend/python/harness_engines/allowlist.json`: opencode, Codex, Claude Code,
and the ACP agents (`acp-opencode`, `acp-gemini`) from `backend/python/harness_engines/acp_agents.json`. ACP
engines run only when an org ceiling names them (`opted_in` in `backend/python/harness_core/engines/profiles.py`).

Workspace → **AI settings** (`/workspace/ai-access`) holds **Default coding engine for the org** and **Hand long work
to the coding engine** (`backend/nodejs/apps/tests/modules/agent-harness/engpick-coding-engine.http.test.ts`).

## Websites and git hosts

Sandboxes have no direct internet. They reach only PipesHub's gateways, through the egress proxy (see the
[security whitepaper](security-whitepaper.md)). An agent reads the web through PipesHub's own web search and page
fetch tools; no website is reachable from a sandbox. The proxy refuses every other destination
(`backend/python/harness_core/sandbox/egress/sites.py`).

Runs with web access may clone **public repositories from github.com, gitlab.com and bitbucket.org** (ADR-REPO-01,
`default_repository_policy` in `backend/python/app/harness/repositories.py`). A sandbox never reaches a git host
itself: PipesHub clones over https outside the sandbox after the person approves, and hands the copy over read-only.
Every clone asks, and **Allow for this conversation** covers that one repository only. The clone's bytes count against
the run's egress cap. Proven by `backend/python/tests/unit/harness/test_gitf_repository_fetch.py` (GITF-02, GITF-03,
GITF-08, GITF-10) and `backend/python/tests/unit/harness/engines/test_r32repo_public_repositories.py`. The operator
tunes storage with `HARNESS_REPO_CACHE_MAX_MB`, `HARNESS_REPO_MAX_MB`, `HARNESS_REPO_FETCH_CONCURRENCY` and
`HARNESS_REPO_FETCH_TIMEOUT_S`. When the worker cannot write its cache folder (a data volume another user owns, or
one mounted read-only) every clone is refused until the operator makes `/data/pipeshub` writable by the app's user
(uid 1000 in the shipped image); the worker logs `repository.cache_unwritable` with the folder. Proven by
`backend/python/tests/unit/harness/test_wp84_repository_cache_unwritable.py` (WP84-GITH-1..4).

## Budgets

**Per run.** The release limits each run to USD 5 (`maxCostUsd`), 15 turns, and a token backstop of 10 million for
coding-engine work and 2 million for the built-in loop (`budgets` in `contracts/harness/core/policy/release.json`).
The USD cap is the real limit; the token caps catch runaways
(`backend/python/tests/unit/harness_core/budget/test_spendcont_caps.py`,
`backend/nodejs/apps/tests/modules/agent-harness/spendcont-token-caps.test.ts`). Agents can only lower them.

**Calls cut short.** When a model call is cut short (Stop, the engine gone, a failed stream) and the provider says
nothing about what it billed, the run is charged an estimate, shown as "estimated (stopped early)" on the run until
the provider's usage replaces it. The rest of the call's upper bound stays held on the run's budget for
`MODEL_GATEWAY_CUT_HOLD_TTL_S` (600 s by default), so nothing is admitted against money the provider may already have
spent (`backend/python/tests/unit/harness_core/gateway/test_wp66_honest_meter.py`,
`frontend/app/(main)/chat/components/message-area/__tests__/wp66-honest-meter.test.tsx`). Nobody sets or releases a
hold by hand.

**Engine runs.** A coding-engine run's steps are a backstop scaled to its money: a run with a USD budget gets
100 steps a dollar, at least 50, never more than its engine profile allows (the release allows 1,000 steps, so its USD 5
run has 500). When 15% of its steps, time or money is left, the engine is told to stop gathering and write what the
task asked for; its later tool calls are refused, and publishing the file it wrote still runs
(`backend/python/tests/unit/harness_core/engines/test_wp70_allowance.py`). A run cut short still ends with an answer
written from what it gathered.

## Kill switches

| Scope | How | What happens |
|---|---|---|
| The whole instance | Operator sets `AGENT_HARNESS_KILL_SWITCH=true` | New runs go to the classic chat. Node cancels the harness runs in flight within 60 s; queued follow-ups are refused. Set on the egress proxy too, it binds no new harness sandbox, so engine runs in flight lose their network |
| One org | Admin turns Labs → **Agent harness** off | New runs of that org go to the classic chat |
| One run, or one person's scheduled runs | The person stops the run from the chat, and pauses their schedules on **Schedules** (`/workspace/schedules`) | The run is cancelled; a paused schedule fires no run until resumed |
| One org's or conversation's network | The egress proxy's `POST /v1/revocations` with the worker credential | Live egress of that org (or conversation) ends |

Proven by `backend/nodejs/apps/tests/modules/agent-harness/harness-rollout.test.ts` (in-flight runs stay reachable
under the kill switch), `backend/python/tests/unit/harness/sandbox_broker/test_wp14_kill_switch_docker.py` and
`backend/python/tests/unit/harness/sandbox_broker/test_egress_security_docker.py` (SEC-CR-03, revoking an org ends
its egress). Changing the engine ceiling while an engine run is in flight is not covered by a test.

## Approvals

**Defaults** (they follow main #4313, `backend/python/harness_core/effects/risk.py`):

- A toolset write runs without asking
  (`backend/python/tests/unit/harness/assembly/test_sync3_tool_rules.py::test_a_toolset_write_runs_without_asking_by_default`).
- A tool whose rule is "ask" waits for a person
  (`backend/python/tests/unit/harness/assembly/test_sync3_tool_rules.py::test_an_ask_tool_asks_through_the_kernel_and_runs_once_approved`),
  and so does an MCP tool that declares no hints
  (`backend/python/tests/unit/harness/assembly/test_sync3_tool_rules.py::test_an_mcp_tool_without_hints_asks_through_the_kernel`).
- Code in the run's own sandbox runs without asking; anything that can reach beyond the sandbox still asks
  (`backend/python/tests/unit/harness_core/effects/test_codeauto_own_sandbox.py`).

The operator can make them stricter for the deployment, or for one agent, in `/services/agentHarness/approvals`
(`default`, `agents[agentKey]`). Nothing stored means the defaults; a stored document the schema refuses asks
(`backend/nodejs/apps/tests/modules/agent-harness/oss-approval-defaults.test.ts`).

**Sending data out** follows the flow guard: after a run has read data that is not public, a call that sends data
out waits for a person (**Ask first**) or is refused (**Never run**), as the operator sets `HARNESS_FLOW_GUARD` with
`HARNESS_FLOW_GUARD_LEVEL` and `HARNESS_FLOW_GUARD_ABOVE`. PipesHub's own web search is not held, and neither is
opening a page whose address came from a search result, a page the agent opened or the person (their message or file,
a record's link); while the flow guard would hold a send, an address the agent made up is refused without a card. No
approval rule, the deployment's included, asks about web search or page opening or blocks them. See the
[security whitepaper](security-whitepaper.md#the-rule-of-two-and-the-flow-guard).

**Coding engine jobs** add one default: a write that leaves the person's own space asks first, and the person may
answer it for the rest of the conversation. A tool that the person, or an admin for the org, has set to
**Pre-approved** in its server's or toolset's **Tool approvals** runs there without asking, as in the chat and saved
agents; with no rule set, the engine's default asks. A **Deny** rule, the flow guard and an approval policy that says
**Ask first** still win over a person's Pre-approved (ADR-OWD-09 D-ALLOW-1;
`backend/python/tests/unit/harness/assembly/test_allow1_engine_tool_allow.py`).

The person a run acts for answers its requests in the conversation's card or from their **Inbox**, one by one or with
**Allow all** and **Deny all** on a card; an org admin may deny another person's request from the conversation.

## Audit

Every audit-relevant event is chained per org with SHA-256, and anchors are written every 15 minutes
(`backend/nodejs/apps/src/modules/agent-harness/domain/audit.ts`;
`backend/nodejs/apps/tests/modules/agent-harness/audit-chain.test.ts`). Tool calls are chained by digest, never by
their arguments.

- Workspace downloads are chained as `workspace.downloaded`, with the path as a digest.
- Control-plane changes are chained as `config.changed` with the actor and the revisions
  (CPD-1).

The app has no page or API that searches or exports the chain: the records are in the chain, not on a page.

## Run settings

Every run uses the release's run settings:

| Section | Settings | Release default |
|---|---|---|
| **Runs at once** | For the whole organization, for each person | 200 and 10 |
| **Tool results** | Longest tool result read in full, the search detail agents may use, the default | Each executor's own cap (64,000 characters in the built-in loop, 24,000 in an engine); concise |
| **Waiting for people** | How long a run waits for an answer; allow answers for the whole conversation | `HARNESS_WAIT_FOR_PERSON_DAYS` (7 days); on |
| **Recovery** | Resume interrupted runs | On |
| **Sandbox resources** | CPUs, memory, processes, scratch space, largest input file | The engine's template; `HARNESS_INPUT_MAX_MB` (100 MiB) |
| **What conversations show** | Model reasoning, terminal output | On |
| **Sandbox snapshots** | How long coding sandbox caches are kept | 7 days |
| **Memories** | Each person's own and per-conversation memories, managed on their **Memories** page, where each person can also turn their own memory off (**Remember things about me**) | On; nothing agent-wide is kept |

**Largest input file** also bounds what comes back: a file larger than it is not written back from the sandbox, and
the rest of the workspace is (`backend/python/tests/unit/harness_core/workspace/sync/test_r14_write_back_limit.py`).
The operator's `HARNESS_INPUT_MAX_MB` is the ceiling.

Elsewhere:

| Setting | Where | Default |
|---|---|---|
| Run limits | Release policy | The release budgets above |
| Engine limits (steps, seconds) | Release policy | 1,000 steps (scaled to the run's money, see [Budgets](#budgets)), 1,800 s (`release.json` `engineProfile.limits`) |
| A run's deadline | Release policy | 1 hour (`runs.deadlineMs`) |
| Conversation retention | Release policy | No automatic expiry: a conversation is kept until its owner deletes it |

The defaults are proven by `backend/nodejs/apps/tests/modules/agent-harness/session-retention.test.ts`, `wp23-snapshot-ttl.test.ts` and `memory-service.test.ts` (PERSONAL_MEMORY) and `memory-personal-switch.test.ts`. Data
classification is an extension point with no default in this build: nothing is classified, and the usual approval
rule decides every ask
(`backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts`).
