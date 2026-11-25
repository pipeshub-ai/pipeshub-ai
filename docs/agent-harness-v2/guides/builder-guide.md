# Agent harness: builder guide

Checklist item C18.04. This page is for people who build agents in the agent builder and for the admins who set
their limits. It covers choosing a harness and a model, tool disclosure, skills, scripted cases, approvals and policy, run
gates, budgets and versions. Labels in **bold** are the English labels in the app. Routes are under `/api/v1` and
are documented in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`.

Some settings have no screen yet and are set through the API. Each section says which.

## How settings combine

Settings come in layers: the release defaults, then the org, then the agent, then the request. A lower layer can
only narrow what the layer above allows. A save that widens it is refused (`CP_WIDENING`, or
`ENGINE_PROFILE_WIDENING` for the engine profile), and a setting an upper layer locked is refused with `CP_LOCKED`.
So an agent can lower a budget, but never raise it past the org's.

The release defaults are in `contracts/harness/core/policy/release.json`.

## Choosing a harness and a model

An agent runs on one of two harnesses:

- **The built-in agent loop.** It answers questions, searches knowledge, calls tools and runs small code in a
  sandbox (`run_code`). Every agent runs on it unless you pick an engine.
- **A coding engine.** A coding agent that works in its own sandbox with files and a shell, for code and long,
  many-step work. Released engines are opencode, Codex and Claude Code, plus Gemini CLI and opencode through the
  generic ACP driver (`backend/python/harness_engines/allowlist.json`). Your org admin chooses which ones the org
  allows. Engine runs need the operator to set `HARNESS_ENGINE_RUNS=true` ([FLAGS.md](../FLAGS.md)).

### In the builder

On the agent's core card:

- **Model** picks the model. With none picked, the card says **Using organization default model**. **Sub-agent
  models** sets the models helpers use.
- **Coding engine** picks the engine. **None – built-in agent loop** keeps the agent on the loop. **Any engine the
  organization allows** lets the chat's choice decide. The helper text says when it is used: "Used when a task needs
  code, files or many steps of work; other questions are answered directly." **Engine settings** opens the engine
  profile below.

The choice is stored as the agent's `engineProfile`. Setting it to `null` turns an engine agent back into a loop
agent.

### Which engine a turn runs

A turn takes the conversation's choice (the `/engine` command), then the person's default (Workspace → **AI
accounts** → **Coding engine**), then the org's (**Default coding engine for the org**). An agent whose profile lists
several engines runs the chosen one when it lists it.

The org setting **Hand long work to the coding engine** (on by default) lets a loop run hand big work to the engine
by itself: up front, when the request is research, analysis across many items, planning or a long report, or
mid-run, when the run crosses the thresholds in `HARNESS_DEEP_ROUTING_THRESHOLDS`. An agent with no engine profile
never hands off.

### The engine profile

The profile (`EngineProfileNarrowing` in the OpenAPI file) narrows what an engine run may do:

| Field | Values | Release default |
|---|---|---|
| `engines` | engine kinds, or `kind@version` | the first released engine |
| `mode` | `plan`, `build`, `review` | `build` |
| `egress` | `none`, or `allowlist:<id>` | `none` |
| `limits` | `maxSteps`, `maxWallS`, `maxTokens`, `maxUsd` | 50 steps, 1800 s |
| `approvals` | per class: `read`, `workspace`, `write`, `execute` | read and workspace allow; write and execute ask |
| `reasoningEffort` | `low`, `medium`, `high` | unset |
| `subAgents` | `enabled`, `maxDepth`, `maxConcurrent` | off |
| `skills`, `tools` | the skills and tool effects the run may use | all effects; skills unset |

Every released engine may be named, the ACP agents (`acp-opencode`, `acp-gemini`, `acp-goose`, `acp-qwen`,
`acp-dsh`) too, where this deployment has its image.

## Tool disclosure

Tool disclosure decides how a run's tools reach the model. There are three modes:

| Mode | What the model sees | Use it when |
|---|---|---|
| `eager` | every granted tool | the agent has a few tools |
| `lazy` | the most-used tools; it finds the rest through `search_tools` and `call_tool` | the agent has many tools |
| `filesystem` | only the meta tools; every tool is described in `/pipeshub/tools` | engine runs. The native loop reads it as `lazy` |

Left unset, each harness keeps its own default. The built-in loop goes lazy only above 20 tools
(`PIPESHUB_LAZY_TOOLS_THRESHOLD`).

There is no screen for it yet. Set it with
`PUT /control-plane/policy/{scopeKind}/{scopeId}/toolDisclosure`, where `scopeKind` is `org` (with `scopeId`
`current`) or `agent` (with the agent key).

## Skills

A skill is a set of instructions, with optional scripts and reference files, that an agent loads when a task needs
it. Skills follow the `SKILL.md` format: front matter with a `name` and a `description`, then a markdown body.

- **Write one** in Workspace → **Your Skills**. Its tabs are Content, Metadata, Resources, Versions and Command.
  You can also import one from npm, a URL, or an upload (`.zip`, `.tar.gz`).
- **Attach one** by dragging it onto the agent's **Skills** section in the builder. The section shows while Labs
  `ENABLE_SKILLS` is on (the default).
- **Write the description for the model.** The model reads it to decide when to load the skill. Say what the skill
  does and when to use it.

Limits (`backend/python/app/agent_loop_lib/modules/providers/skills/validator.py`):

| | Limit |
|---|---|
| Name | 64 characters, kebab-case |
| Description | 1024 characters |
| Body | 200,000 characters; a warning above 500 lines or about 5,000 tokens |
| Resource path | 255 characters |
| Resources | 1 MiB per file, 5 MiB per skill |
| Import archive | 25 MiB, at most 500 files, 100 MiB unpacked |

Skills are versioned: the API has versions, rollback and publish under `/skills/{name}`.

## Ratings and scripted cases

- **Ratings.** A rating of an answer names the run and the agent version that answered, so you can find what a
  thumbs-down was about.
- **Scripted harness cases**, for contributors changing the harness itself. The deterministic cases the parity,
  replay and security tests drive are YAML under `backend/python/tests/support/parity_cases/harness/`: a task,
  fixtures, a scripted model, graders (`contains`, `regex`, `exact`, `status`, `effect`, `no_effects`,
  `tool_called`) and a `safety` block. Every case must be able to fail. The answer-quality evals are in
  `backend/python/tests/evals/README.md`.

A good case states the task, what a correct answer contains, which effects must and must not happen, and that
an empty run fails it.

## Approvals and policy

By default:

- reads run without asking;
- a toolset's writes run; a toolset tool that deletes data asks;
- an MCP tool that only reads runs; one that changes data asks; one that deletes data is blocked;
- code in the run's own sandbox, with no network, runs without asking. A coding engine's own shell commands inside
  its sandbox are not checked one by one;
- once a run has read private data, a call that sends data out of the organization asks (the flow rule).

The operator can make these stricter for the whole deployment, or for one agent, in the configuration store's
`/services/agentHarness/approvals` (`default`, `agents[agentKey]`), and a person can set a tool to **Pre-approved**
or **Ask** in its server's or toolset's **Tool approvals**.

Where rules meet, the strictest wins: deny over ask over allow. The rules' format is
`contracts/harness/core/approval-rules.schema.json`.

The agent builder has no approval editor of its own.

## Run gates

Run gates make one run safer to try. They are set per request, in the `gates` field of the chat and run request
bodies (`TurnRunGates` in the OpenAPI file). There is no screen to set them yet; the chat shows their results.

| Gate | What it does |
|---|---|
| `dryRun: true` | Reads run. Every write, send, delete or other outside effect is recorded as what the run would have done, and never made |
| `planApproval: "required"` | Only reads run until a person approves the plan the run submits. A declined plan ends the run |
| `verify` | Checks the result before the run ends: a workspace file exists and opens, a file matches a JSON schema, the answer's citations hold, or a command passes. Up to 16 checks; `onFail` is `flag` or `continue`; `maxRounds` 1–3 |

A request with gates on a chat the harness does not serve is refused with `gates_need_harness`.

## Budgets

Every run has a budget. The release defaults are:

| Limit | Default |
|---|---|
| Cost per run | USD 5 |
| Turns per run | 15 |
| Tokens per run | 10 million for engine runs and loops that can hand off; 2 million for loops that cannot |
| Active time | none |

The USD cap is the real limit. The token caps only stop a runaway.

- **Org:** Workspace → **AI accounts and access** → **Run limits**: **Max turns per run**, **Max cost per run
  (USD)**, **Max tokens per run**, **Active time (minutes)**. Agents can only lower them.
- **Agent:** `PUT /control-plane/policy/agent/{agentKey}/budgets`. There is no builder screen for it yet.
- **Engine runs:** also `limits.maxUsd` and `limits.maxTokens` in the engine profile.
- **Schedules:** each schedule may set a lower budget per run, at most USD 5.

Each run holds back a small reserve for its last answer. When a run reaches its budget, it answers from what it
gathered so far, and the chat shows "Stopped early: spending limit reached" under the answer. The person can send
"continue" to carry on. An engine task or a helper that is cut short hands back its partial work.

## Run limits, results and what the chat shows

These settings have no screen yet. Set them with
`PUT /control-plane/policy/{scopeKind}/{scopeId}/{section}`, where `section` is the part before the dot. Like every
setting, a lower layer may only narrow them, and an org admin may lock them (`locks`). The release defaults are in
`contracts/harness/core/policy/release.json`.

| Setting | Who sets it | Default | What it does |
|---|---|---|---|
| `quotas.activeRunsPerOrg` | deployment, org | 200 | Runs one org may have queued or running at once. One more is refused with a retry time. |
| `quotas.activeRunsPerUser` | deployment, org | 10 | Runs one person may have queued or running at once. |
| `toolResults.maxChars` | deployment down to the request | each harness's own (loop 64,000 characters, engine 24,000) | The most of one tool result the model reads inline. Past it, an engine's knowledge results are saved under `/pipeshub/knowledge` and other results keep their start and end, with how to get the rest. A set value lowers both harnesses' caps. |
| `toolResults.formats` | deployment down to the request | any | The `response_format` values an agent may default to: `concise`, `detailed`. |
| `toolResults.format` | org, team, agent | `concise` | What a knowledge search returns when the model does not say: `concise` (the most relevant passages) or `detailed` (more of each). |
| `interactions.waitS` | deployment down to the request | the deployment's `HARNESS_WAIT_FOR_PERSON_DAYS` (7 days) | How long a run waits for an answer to an approval or a question before it ends as expired. An admin sets it per org, team or agent only where the deployment offers it (the policy API's edition dimensions); otherwise every run waits the deployment's window. |
| `interactions.conversationGrants` | deployment down to the request | on | Whether a person may approve a call "for this conversation". Off: every call is asked. |
| `recovery.autoResume` | deployment, org, team, agent | on | Whether a run whose worker was lost resumes from its checkpoint. A call whose outcome is unknown is never repeated either way. Off: the run ends as interrupted and the person sends the message again. |
| `sandbox.cpus`, `sandbox.memoryMb`, `sandbox.pids`, `sandbox.scratchMb` | deployment down to the request | the engine template's | Lower an engine run's sandbox CPUs, memory, processes and scratch disk. They never raise the template's values. |
| `sandbox.inputMaxMb` | deployment, org | the deployment's `HARNESS_INPUT_MAX_MB` (100 MiB) | Lower the largest file, in MiB, a run is given as an input. It never raises the deployment's; an input past it is refused before any of it is read. |
| `ui.reasoning` | deployment, org, team, agent | on | Whether the chat shows the model's reasoning, where the model exposes it. Off: reasoning is never sent to the browser. |
| `ui.terminal` | deployment, org, team, agent | on (read-only) | Whether the chat shows an engine run's read-only terminal. Off: commands are listed by name and their output is not sent to the browser. |

What every row of the configuration table is carried by (a setting, an operator switch or a section) is in
`contracts/harness/config-surface.json`.

## Versions: publish and roll back

The agent you edit is the draft. Chats run the published version.

- **Save draft** keeps your edits "without changing what chats run".
- **Publish** makes your edits the version chats run. You can add a note on what changed. "Schedules keep the
  version they run until their owner moves them."
- **Versions** lists every published version: who published it, and when.
- **Roll back to this version** publishes an old version again as a new version. It also resets the draft to that
  version, so "Your unpublished changes will be discarded." History only grows.

The header shows **Live: version N** and **Unpublished changes** when the draft differs. The API is
`/agents/{agentKey}/versions`.

Coming: exporting an agent's whole configuration and importing it into another org. The API is built
(`/agents/{agentKey}/export`, `/agents/import`); the screen is not yet.

## See also

- [User guide](./user-guide.md): what people see when they chat.
- [FLAGS.md](../FLAGS.md): every switch, who may change it, and its default.
- [Vendor DPA checklist](./vendor-dpa-checklist.md): before you turn on a hosted model provider or engine.
