# Master test plan: end to end

**Status:** plan, 2026-10-09. **Data:** [`data/e2e-factors.json`](data/e2e-factors.json),
[`data/e2e-must-run.json`](data/e2e-must-run.json), [`data/e2e-pairwise.csv`](data/e2e-pairwise.csv),
[`data/e2e-threeway-core.csv`](data/e2e-threeway-core.csv). **Generated:** §3's scenario details and
[`generated/e2e-matrix.md`](generated/e2e-matrix.md) by `scripts/e2e_covering_array.py --render`.

## 1. What end to end means here

An e2e test drives the shipped deployment from its outermost surface (browser, Slack-shaped internal route, the
published MCP client, or a schedule firing) through Node, the services, the worker, the sandbox plane and a model,
and asserts on **four** things, not only the screen:

1. **What the user sees** (Playwright, or the SSE frames for API-level rows);
2. **The journal**: the run's events in order, plus the history checks every X/E test runs
   (gap-free `seq`, one `run.spec_pinned` first, every `tool.outcome` after its `tool.intent`, nothing after the
   terminal event except retention markers; TEST-STRATEGY §2.14);
3. **The effects**: what the fake SaaS (`deployment/e2e/fake_saas/server.py`) and the fake MCP server recorded,
   keyed by idempotency key, so "exactly once" and "never" are checkable;
4. **The audit and the operator surface**: the org's audit chain verifies and holds the expected entries;
   metrics (`pipeshub_agent_chat_turns_total{path}`), logs (no canary strings) and admin views
   (`effectivePolicy.heldRequests`, the unknown-effects console) agree.

Two modes:

- **Scripted (Es):** fake LLM (`deployment/e2e/fake_llm/server.py`) serves every wire (chat completions,
  Responses, Messages) from scripts chosen by a marker in the prompt. Runs in CI; deterministic; gating.
- **Live (El):** real models, a registered run plan committed before the run (the rule every live decision in
  this project followed: P4.12–P4.16, E2E4), a budget, and pass rules such as "2 of 3 trials per cell". Never
  gating a PR; gating a release.

Lanes used below: **Es-PR** (scripted, on PRs that touch the harness; stack ST-1), **Es-nightly** (scripted, all
stacks over the week), **El-release** (live, per release), **K8s-kind** (Helm on kind), **Iso-gVisor** and
**Iso-Kata** (runners the default CI does not have; see 00 §6).

## 2. The harness options matrix

### 2.1 Factors

| Factor | Levels | Notes |
|---|---|---|
| mode | quick, auto, react, deep, plain, internal_search, universal, plan_execute, project | plain = assistant chat without tools; universal = the default chat's Agent mode as the web app sends it (`chatMode: agent`, `harness: durable` since ASSIST1), durable, and an engine row of it is a job the default chat hands to the org's coding engine (R31JOURNEY-FU-7); plan_execute and project are legacy-only today and move to durable as LAT1/CPD-10 land. Each mode's routing input is `contracts/harness/vectors/routing.json` `e2e.modes`, which the oracle and the row runner both read; `assist1-harness-request.test.ts` holds it to the web app's request builder, so it cannot drift from the request the product makes |
| action | send, regenerate, reconnect | reconnect = drop SSE mid-stream, resume with `Last-Event-ID` |
| path | durable, kill_switch, standalone_mongo | the deployment setting; the effective route also depends on the mode |
| engine | none, opencode, codex, claude_code | Claude Code 2.1.294 is admitted (CC1); its rows run like the others' (QA-31) |
| child_model | none, same, different, forbidden | the agent's `roleModels` (MA1, QA-37): none = no role model, a child calls its parent's model as before; same / different = the role `default` is set to the turn's own model / another allowed model; forbidden = to a model the org's `models.routes` excludes (enforced with `CONTROL_PLANE_PIN=on`) |
| wire | openai_chat, openai_responses, anthropic_messages, azure_openai, gemini, bedrock | scripted lanes serve all from the fake LLM |
| compiler | off, on | `HARNESS_CONTEXT_COMPILER` |
| flow_guard | off, ask, org_deny | `HARNESS_FLOW_GUARD`; org_deny = the org's approval policy sets the flow rule to deny |
| write_policy | allow, ask | `/services/agentHarness/approvals` writes |
| decision | none, allow_once, allow_conversation, deny, expire | what the user does when a card appears; `none` exactly when the oracle expects no card |
| tool_family | none, knowledge, mcp, toolset, run_code, skills, memory | sink families (mcp, toolset) run a read-then-write script |
| sandbox | unset, docker, broker_closed, broker_allowlist, broker_open | isolation runtimes are a separate lane (§5.4) |
| trigger | interactive, schedule_cron, schedule_webhook | |
| graph | neo4j, arango | |
| mongo | replset, standalone | |
| kv | redis, etcd | |
| broker | kafka, redis_streams | |
| deploy | compose, helm | |
| role | user, admin, operator | the surfaces asserted besides the chat: admin views; metrics, logs and health |

The full cartesian product is 4,702,924,800 combinations. The plan runs **55 must-run scenarios** (§3), a
**pairwise covering array of 132 rows** over all 19 factors and a **3-wise array of 75 rows** over the trust core
(path, flow_guard, write_policy, decision, tool_family): **262 scenarios** in all. Every valid pair of levels
of any two factors (2,037 pairs) appears in at least one pairwise row, and every valid triple over the trust core
(293 triples) in at least one 3-wise row.

`child_model` is covered pairwise and is not in the trust core. A forbidden role model is a trust decision, but it
is made once, at the spawn, before any tool call: the spawn is refused whatever the flow guard, the write policy
and the tool family are, so its triples with them would each repeat one outcome. With it in the core the 3-wise
array grows from 73 to 150 rows (293 to 729 triples); pairwise already puts every level against every path, tool
family, flow-guard level and decision it can meet. Rows committed before the factor keep their ids and take
`none` (PW-102..PW-127 are the new ones).

### 2.2 Constraints

Combinations that cannot happen are excluded, so no row tests nothing. They are code
(`scripts/e2e_covering_array.py`, `CONSTRAINTS`) and copied into each generated array:

| Constraint | Rule |
|---|---|
| stack_profile | the store and deployment factors take one of seven stack profiles (§4.1) |
| standalone_is_infra | a standalone MongoDB is exactly the `standalone_mongo` path |
| engine_needs_durable_agent | engine runs are durable runs of an engine agent in quick, auto, react or deep, or of the default chat's Agent mode (universal) handing a job to the org's coding engine; the modes are the routing table's (`routing_table.e2e_engine_modes()`), not a list here |
| engine_needs_broker | an engine needs `SANDBOX_MODE=broker` |
| engine_wire | opencode speaks chat completions (OpenAI or Azure), Codex speaks Responses (OpenAI or Azure), Claude Code speaks Messages |
| compiler_is_loop_only | the compiler runs only on durable loop runs |
| memory_is_durable | governed memory is absent on the legacy route |
| memory_needs_a_person | a scheduled run is unattended and is granted no memory write scope (P7.3), so it is offered no `remember` tool |
| run_code_needs_sandbox | `run_code` is hidden without `SANDBOX_MODE` |
| mode_tool_families | plain: none or memory; internal_search: knowledge or memory; project: none or knowledge |
| scheduled_runs | schedules fire durable agent runs and send a fresh turn |
| helm_sandbox_modes | the chart offers broker (and local), not docker mode |
| decision_iff_prompt | a decision is set exactly when the oracle expects a card (§4.3); a refused spawn (`child_model=forbidden` on the durable route) shows none |
| child_model_needs_delegation | a role model (same, different, forbidden) needs a turn that spawns a child run: deep mode on a loop run. An engine run's helpers are not child runs and call the run's own model (MA1 §6). The legacy route is kept: its rows are planned and reported blocked (§4.2), not removed |
| conversation_grant_offer | "Allow for this conversation" only for a call that names its destination (the toolset's project; the fake MCP server's write names none), held by the flow rule or an engine's default alone: an org's own write rule is never granted (QA31-F1) |

## 3. Must-run scenarios

High-risk combinations named explicitly, each tied to a risk in [00](00-scope-and-strategy.md) §2. The table and
the details below are rendered from [`data/e2e-must-run.json`](data/e2e-must-run.json); edit the JSON and run
`python3 scripts/e2e_covering_array.py --render`. Factor values not listed take the defaults: quick, send,
durable, no engine, openai_chat, compiler off, flow guard ask, writes allow, no decision, knowledge,
broker_closed, interactive, Neo4j, replica set, Redis, Redis Streams, Compose, user, no role model.

<!-- BEGIN generated: must-run (scripts/e2e_covering_array.py --render) -->

| Id | Scenario | Lane | Runs | Factors (others at defaults) | Today |
|---|---|---|---|---|---|
| EM-01 | Durable quick turn with a knowledge read: the golden path | Es-PR | scripted | defaults | partial: frontend/tests/e2e/chat/chat-durable-stream.spec.ts (F-1, F-2; dev-only lane), backend/python/tests/cohesion/test_c24_durable_wire_parity.py (offline) |
| EM-02 | Flow guard asks after a private read; Allow once runs the write exactly once | Es-PR | scripted | tool_family=toolset, decision=allow_once | partial: frontend/tests/e2e/chat/chat-approval-card.spec.ts (UI-5, CA-8; dev-only lane) |
| EM-03 | Flow guard: Deny sends nothing and the agent says so | Es-PR | scripted | tool_family=mcp, decision=deny | new |
| EM-04 | Approval expires: nothing sent, run ends with a clear message | Es-nightly | scripted | tool_family=toolset, write_policy=ask, decision=expire | partial: backend/nodejs/apps/tests/modules/agent-harness/interactions.test.ts (unit) |
| EM-05 | Allow for this conversation: the next matching write does not ask; a new conversation asks again | Es-nightly | scripted | tool_family=toolset, decision=allow_conversation | partial: frontend/tests/e2e/chat/chat-approval-card.spec.ts (CA-8), backend/nodejs/apps/tests/modules/agent-harness/ux1-conversation-grant.test.ts |
| EM-06 | Org deny rule refuses without a card | Es-nightly | scripted | tool_family=mcp, flow_guard=org_deny, role=admin | partial: backend/python/tests/unit/harness/test_sec2_flow_per_org.py (unit) |
| EM-07 | Regenerate after a denied write is durable and asks again | Es-nightly | scripted | tool_family=toolset, action=regenerate, decision=deny | partial: backend/nodejs/apps/tests/modules/agent-harness/ux3-regenerate.mongo.itest.ts (I) |
| EM-08 | Kill switch mid-flight | Es-nightly | scripted | tool_family=toolset, flow_guard=off, role=operator | partial: DUR3 kill-switch test (backend/nodejs/apps/tests/modules/agent-harness/harness-rollout.test.ts) is unit-level |
| EM-09 | Legacy path refuses an external write after a read | Es-nightly | scripted | mode=plan_execute, tool_family=toolset | partial: backend/python/tests/unit/harness/test_sec1_flow_guard_loop.py (legacy refusal, unit) |
| EM-10 | Server decides routing: legacy-only modes and project chats, one request | Es-nightly | scripted | mode=project, tool_family=knowledge | partial: frontend/app/(main)/chat/__tests__/project-scope.test.ts (unit) |
| EM-11 | Worker SIGKILL mid-tool; resume on another worker; effect once | Es-nightly | scripted | tool_family=toolset, flow_guard=off, role=operator | partial: integration-tests/harness/fault/test_crash_points.py (write_after_effect_idempotent; API-level, no browser) |
| EM-12 | Lost acknowledgement leaves an unknown effect; card and console resolve it | Es-nightly | scripted | tool_family=toolset, flow_guard=off, role=admin | partial: integration-tests/harness/fault/test_crash_points.py (write_after_effect_not_idempotent), frontend unit ux3-unknown-effect-card.test.tsx |
| EM-13 | Stop during a long model stream | Es-PR | scripted | tool_family=none, mode=auto | partial: frontend/tests/e2e/chat/chat-durable-stream.spec.ts (F-3; dev-only) |
| EM-14 | Reconnect across a rolling Node restart | Es-nightly | scripted | action=reconnect, tool_family=none | partial: frontend/tests/e2e/chat/chat-durable-stream.spec.ts (F-2 reload), integration-tests/harness/fault/test_upgrades.py (ru4a, API-level) |
| EM-15 | Deep mode: a sub-agent's approval reaches the main thread | Es-nightly | scripted | mode=deep, tool_family=toolset, decision=allow_once | partial: backend/nodejs/apps/tests/modules/agent-harness/projection/ux3-child-approvals.test.ts (unit) |
| EM-16 | Engine run with opencode end to end, with approval and egress check | Es-nightly | scripted | engine=opencode, tool_family=toolset, decision=allow_once, sandbox=broker_allowlist | exists (dev-only): frontend/tests/e2e/chat/chat-durable-engine-e2e.spec.ts (DE-1..DE-4, DE-6) via deployment/e2e/run.sh durable |
| EM-17 | Codex engine run; worker killed mid-turn; one writer per workspace | Es-nightly | scripted | engine=codex, wire=openai_responses, tool_family=run_code, sandbox=broker_closed | partial: frontend/tests/e2e/chat/chat-durable-engine-e2e.spec.ts (DE-5, opencode only) |
| EM-18 | Claude Code engine run (Messages wire) | El-release | live | engine=claude_code, wire=anthropic_messages, tool_family=mcp, sandbox=broker_allowlist, decision=allow_once | partial: backend/python/tests/unit/harness/engines/test_qa31_claude_code_rows.py (scripted, engine lane) and test_e2e4_live_matrix.py MC/claude_code (live, 3 of 3 on 2026-10-11; QA-31); no browser |
| EM-19 | run_code on the durable loop with no network | Es-nightly | scripted | tool_family=run_code, sandbox=broker_closed, mode=react | partial: backend/python/tests/unit/harness/sandbox_broker/test_durable_run_code_docker.py (Docker lane, no browser) |
| EM-20 | run_code with open egress: public allowed, private and metadata refused | Es-nightly | scripted | tool_family=run_code, sandbox=broker_open | partial: backend/python/tests/unit/harness/sandbox_broker/test_open_egress_firewall.py (Docker lane) |
| EM-21 | MCP hub: unapproved tools absent; approve; drift disables | Es-nightly | scripted | tool_family=mcp, role=admin, flow_guard=off | partial: frontend/tests/e2e/workspace/u4-manifest-approval.spec.ts; pin digest: backend/python/tests/unit/harness/assembly/test_descriptor_pins.py |
| EM-22 | STDIO MCP server in a runner sandbox with an egress allowlist | Es-nightly | scripted | tool_family=mcp, sandbox=broker_allowlist, flow_guard=off | partial: backend/python/tests/unit/harness/sandbox_broker/test_mcp_hub_runner_docker.py, test_egress_tunnel_docker.py |
| EM-23 | Two instances of one toolset keep their own credentials | Es-nightly | scripted | tool_family=toolset, flow_guard=off, role=operator | partial: backend/python/tests/unit/harness/assembly/test_instance_tool_ids.py (unit) |
| EM-24 | Missing user credential: item unavailable, agent still answers | Es-nightly | scripted | tool_family=toolset, flow_guard=off | new |
| EM-25 | Skills are private to their creator | Es-nightly | scripted | tool_family=skills | partial: backend/python/tests/unit/agents/agent_loop/skills/test_skill_visibility.py (unit, both loops) |
| EM-26 | Governed memory: proposal, review, use, erase | Es-nightly | scripted | tool_family=memory, role=admin | partial: backend/nodejs/apps/tests/modules/agent-harness/memory-service.test.ts (unit) |
| EM-27 | Scheduled cron run: once per slot, as its creator, re-authorized | Es-nightly | scripted | trigger=schedule_cron, tool_family=toolset, write_policy=ask, decision=allow_once | partial: frontend/tests/e2e/workspace/u4-schedules.spec.ts (CRUD only) |
| EM-28 | Webhook-triggered schedule: duplicate delivery fires once | Es-nightly | scripted | trigger=schedule_webhook, tool_family=knowledge | new |
| EM-29 | Delete a conversation: everything erased; backfill for older deletions | Es-nightly | scripted | tool_family=run_code, sandbox=broker_closed, role=operator | partial: backend/nodejs/apps/tests/modules/agent-harness/session-erasure.mongo.itest.ts, erasure-backfill.mongo.itest.ts (I) |
| EM-30 | Retention erases idle conversations | Es-nightly | scripted | tool_family=none, role=admin | partial: backend/nodejs/apps/tests/modules/agent-harness/session-retention.test.ts (unit, real timers: R3) |
| EM-31 | Shared conversation: collaborator turn and continue privately | Es-nightly | scripted | tool_family=knowledge | partial: backend/nodejs/apps/tests/modules/agent-harness/collaborator-turns.mongo.itest.ts, frontend/app/(main)/chat/__tests__/continue-privately.test.tsx |
| EM-32 | Cross-org isolation over every harness route | Es-PR | scripted | tool_family=knowledge, role=admin | partial: QA-26 replays every harness route through the Node composition on a real MongoDB (backend/nodejs/apps/tests/modules/agent-harness/qa26-cross-tenant.mongo.itest.ts, route table held by qa26-route-inventory.test.ts); no browser-level run |
| EM-33 | Upgrade with runs in flight, both orders, and rollback | Es-nightly | scripted | tool_family=toolset, flow_guard=off, role=operator | partial: integration-tests/harness/fault/test_upgrades.py, deployment/docker-compose/tests/upgrade_smoke.sh |
| EM-34 | Helm install on kind: durable chat at chart defaults | K8s-kind | scripted | deploy=helm, tool_family=knowledge | partial: deployment/helm/tests/kind_smoke.sh (install only) |
| EM-35 | Alternative stores: ArangoDB, etcd, Kafka on Compose | Es-nightly | scripted | graph=arango, kv=etcd, broker=kafka, tool_family=knowledge, decision=none | new (E2E4 ran ArangoDB knowledge cells at API level) |
| EM-36 | Standalone MongoDB: legacy, explained | Es-nightly | scripted | path=standalone_mongo, mongo=standalone, kv=etcd, tool_family=knowledge | partial: deployment/docker-compose/tests/mongodb_replset_test.sh (topology only) |
| EM-37 | Model failures: retry and fallback when the policy is on | Es-nightly | scripted | tool_family=none, wire=anthropic_messages | partial: backend/python/tests/unit/harness_core/durable/test_model_policy.py (unit) |
| EM-38 | A runaway reply is cut and said so | Es-nightly | scripted | tool_family=none | partial: backend/python/tests/unit/harness_core/models/test_output_bound.py, harness_replay/test_rig_output_limit.py |
| EM-39 | Context compiler on a long session (live) | El-release | live | compiler=on, tool_family=knowledge, wire=azure_openai | partial: backend/python/tests/contracts/test_compiler_long_sessions.py (scripted) |
| EM-40 | Narrowing applies to running work; widening waits for the next admission | Es-nightly | scripted | tool_family=toolset, decision=allow_once, role=admin | new |
| EM-41 | Install, approve, grant, chat through the control plane | Es-nightly | scripted | tool_family=mcp, role=admin, flow_guard=off | new |
| EM-42 | Isolation under gVisor | Iso-gVisor | scripted | tool_family=run_code, sandbox=broker_closed, engine=opencode | needs a host with runsc; written for the gVisor runner (QA-25) |
| EM-43 | Isolation under Kata | Iso-Kata | scripted | tool_family=run_code, sandbox=broker_closed | needs a host with KVM; Kata runner (QA-25) |
| EM-44 | Red team: planted injections do not exfiltrate (live) | El-release | live | tool_family=mcp, wire=azure_openai, decision=deny | partial: backend/python/tests/unit/harness/security/redteam (H7); engine-side drivers left to a human pen test |
| EM-45 | Accessibility and translation of harness screens | Es-nightly | scripted | tool_family=toolset, decision=allow_once, role=admin | new |
| EM-46 | MCP client and Slack surfaces on the durable path | Es-nightly | scripted | tool_family=knowledge | partial: integration-tests/mcp_package (published package), integration-tests/harness/fault/test_surface_leaks.py |
| EM-47 | Steering: a message while the run works is added to the task | Es-nightly | scripted | tool_family=knowledge, mode=react | partial: integration-tests/harness/fault/test_crash_points.py (steer_*), backend/nodejs/apps/tests/modules/agent-harness/steering.http.test.ts |
| EM-48 | Activity panel: a run_code program's output streams live, survives a reload and stays the owner's | Es-nightly | scripted | tool_family=run_code, sandbox=broker_closed, mode=react | partial: frontend/tests/e2e/chat/chat-activity-panel.spec.ts (mocked SSE; dev-only lane), backend/python/tests/unit/harness/assembly/test_ux5_run_code_activity.py, backend/nodejs/apps/tests/modules/agent-harness/ux5-activity-route.http.test.ts |
| EM-49 | Suggested next steps: chips after the answer fill the input and never send; the call is charged to the run | Es-nightly | scripted | tool_family=knowledge | partial: frontend/app/(main)/chat/components/message-area/__tests__/ux5-followup-chips.test.tsx (unit), backend/python/tests/unit/harness/test_ux5_followups.py (unit) |
| EM-50 | Deep mode: a sub-agent whose role is set to another model calls that model, and only it does | Es-nightly | scripted | mode=deep, tool_family=knowledge, child_model=different | partial: backend/nodejs/apps/tests/modules/agent-harness/ma1-child-spawn.test.ts (QA37-I1), backend/python/tests/unit/harness/pinning/test_ma1_child_models.py (QA37-I2); no deployment |
| EM-51 | Deep mode: a role model the org does not allow refuses that spawn; the parent is told and answers | Es-nightly | scripted | mode=deep, tool_family=toolset, child_model=forbidden, role=admin | partial: backend/nodejs/apps/tests/modules/agent-harness/ma1-child-spawn.test.ts (QA37-I4); no deployment |
| EM-52 | Engine run (opencode): the agent's own model, not the org default, reaches the provider through the model gateway | Es-nightly | scripted | engine=opencode, tool_family=none | partial: backend/python/tests/unit/harness_core/gateway/test_ma1_gateway_child_model.py (QA37-I3: the gateway serves the grant's first route on each wire; no deployment) |
| EM-53 | Engine run (Codex): the agent's own model, not the org default, reaches the provider through the model gateway | Es-nightly | scripted | engine=codex, tool_family=none, wire=openai_responses | partial: backend/python/tests/unit/harness_core/gateway/test_ma1_gateway_child_model.py (QA37-I3: the gateway serves the grant's first route on each wire; no deployment) |
| EM-54 | Engine run (Claude Code): the agent's own model, not the org default, reaches the provider through the model gateway | Es-nightly | scripted | engine=claude_code, tool_family=none, wire=anthropic_messages | partial: backend/python/tests/unit/harness_core/gateway/test_ma1_gateway_child_model.py (QA37-I3: the gateway serves the grant's first route on each wire; no deployment) |
| EM-55 | A role's model withdrawn mid-run: the sub-agent's claim fails clearly, never on another model | Es-nightly | scripted | mode=deep, tool_family=knowledge, child_model=different, role=admin | partial: backend/python/tests/unit/harness/pinning/test_ma1_child_models.py (QA37-I5); no deployment |
| EM-56 | R31-SECKEY: no org or platform credential enters an engine sandbox, no upstream is reachable from one but through the gateways, and a sandbox's tokens reach only its run's grants | Es-nightly | scripted | engine=opencode, tool_family=mcp, decision=allow_once, sandbox=broker_closed | deployment/e2e/seckey.py (run.sh seckey RUN); readers: backend/python/tests/unit/harness/test_r31seckey_canary_lane.py |
| EM-57 | R32-TRUST: at the production floor (gVisor, no dev override) every release engine is offered and runs, its ACP profiles verified against the stack's own test root; an untrusted profile reads "not signed for this deployment", never "not installed" | Iso-gVisor | scripted | engine=opencode, tool_family=mcp, decision=allow_once, sandbox=broker_closed, role=admin | deployment/e2e/smoke_human.py and deployment/e2e/journeys.py --tier gate on an E2E_SANDBOX_RUNTIME=runsc human-up stack; readers: backend/python/tests/cohesion/test_r32_stack_trust.py, backend/python/tests/unit/harness_engines/test_r32_profile_trust.py, backend/nodejs/apps/tests/modules/agent-harness/r32-engine-profile-untrusted.test.ts, frontend/app/(main)/workspace/ai-accounts/__tests__/r32-engine-profile-untrusted.test.ts |
| EM-58 | R38-IDENT (SEC-C-03, ADR-SLACKID-01): a Slack person acts only as the PipesHub account they linked; a spoofed profile email, a Slack Connect outsider, a guest and a shared channel are refused privately with no lookup | Iso-gVisor | scripted | tool_family=knowledge, trigger=interactive | deployment/e2e/journeys.py (slack_identity_holds, link_slack_person) on a human-up stack; readers: backend/nodejs/apps/tests/integrations/slack-bot/r38-slack-identity.test.ts, backend/nodejs/apps/tests/modules/agent-harness/sec-c-platform-appsec.test.ts, frontend/app/(public)/slack/link/__tests__/slack-link-view.test.tsx |
| EM-59 | R41-SUPPLY (SEC-D-04, ADR-SEC-05): install.sh runs only a release image whose signature it checked, by digest; a re-tagged unsigned image is refused plainly and starts nothing | Es-PR | scripted | defaults | deployment/docker-compose/tests/image_signature_test.sh; backend/python/tests/unit/harness/security/test_sec_d_ops_supply_chain.py (SEC-D-04) |
| EM-60 | R41-SUPPLY (SEC-D-09, ADR-OPS-04): on a cluster, the broker and its sandboxes reach neither cloud metadata nor the stores, and the app still works | K8s-kind | scripted | deploy=helm | deployment/helm/tests/netpol_check.py, test_netpol.py (rendered); the cluster run is R41SUPPLY-FU-4 |
| EM-61 | R42-SMALL (R38IDENT-FU-13): a deep agent's sub-agent calls the MCP hub tool its coordinator listed at the claim, with no second listing of the STDIO server | Iso-gVisor | scripted | mode=deep, trigger=interactive | deployment/e2e/journeys.py J-agent.deep-seeded-hub on a human-up stack; readers: backend/python/tests/unit/harness/assembly/test_r42small_deep_sub_agent_pins.py |
| EM-62 | R41-OPS (SEC-D-07, SEC-C-14, ADR-WEB-01): the signed-in app's main pages load under the enforced CSP with no console violation, an injected inline script does not run, and no page can be framed | Iso-gVisor | scripted | tool_family=knowledge, trigger=interactive | frontend/tests/e2e/security/browser-security-headers.spec.ts on a human-up stack (and every base-fixture spec fails on a CSP violation); readers: backend/nodejs/apps/tests/libs/middlewares/security-headers.test.ts, backend/nodejs/apps/tests/app.test.ts |
| EM-63 | R41-OPS (SEC-D-06, SEC-D-08, ADR-OPS-03): every bundled store refuses an anonymous client, the app refuses an open store with what to set, and the app port is local unless the operator opens it | Iso-gVisor | scripted | tool_family=knowledge, trigger=interactive | deployment/docker-compose/tests/store_auth_test.sh, deployment/docker-compose/tests/installer_test.sh; readers: backend/python/tests/unit/harness/security/test_sec_d_store_auth.py, backend/nodejs/apps/tests/libs/config/store-auth.test.ts, deployment/helm/tests/test_store_auth.py |
| EM-64 | R43-AUTH (R41OPS-FU-7, R41OPS-FU-8, ADR-WEB-02): the web session survives a reload and a new tab with no token in page storage, an older build's localStorage tokens move to the HttpOnly cookie, and the unauthenticated health routes name no store | Iso-gVisor | scripted | tool_family=knowledge, trigger=interactive | frontend/tests/e2e/auth/session-persistence.spec.ts on a human-up stack; readers: backend/nodejs/apps/tests/modules/auth/routes/session-cookie.http.test.ts, backend/nodejs/apps/tests/modules/tokens_manager/routes/health.routes.test.ts, frontend/lib/auth/__tests__/session-restore.test.ts, frontend/lib/__tests__/no-token-in-web-storage.test.ts |
| EM-65 | R44-IDENT (R43AUTH-FU-1, ADR-WEB-02 D9): every refresh replaces the refresh token; two tabs refreshing together stay signed in; a replayed old cookie ends that sign-in and is audited | Iso-gVisor | scripted | tool_family=knowledge, trigger=interactive | readers: backend/nodejs/apps/tests/modules/auth/session-rotation.test.ts, backend/nodejs/apps/tests/modules/auth/controller/userAccount.controller.test.ts, frontend/lib/auth/__tests__/session-rotation.test.ts; Playwright spec not yet written (R44IDENT-FU-1) |

#### EM-01 Durable quick turn with a knowledge read: the golden path

- **Risks (00 §2):** #12, #11. **Lane:** Es-PR (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Seed org A (admin, member) and a knowledge base with one record the member can read.
  2. Member opens an agent chat (quick) and asks a question; fake LLM script `read-then-answer` calls the search tool once.
  3. Reload the page after the answer.
- **User sees:** First frame `conversation_created` within SLO #3; answer streams with one citation; after reload the same answer, citation and timeline render (live == reload).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); run.admitted, run.spec_pinned, model.response x2, tool.intent/tool.outcome (read), x.pipeshub.answer.finalized, run.completed in that order.
- **Effects:** fake SaaS records 0 effects.
- **Audit:** audit chain verifies for org A; run.spec_pinned is chained.
- **Covered today:** partial: frontend/tests/e2e/chat/chat-durable-stream.spec.ts (F-1, F-2; dev-only lane), backend/python/tests/cohesion/test_c24_durable_wire_parity.py (offline).

#### EM-02 Flow guard asks after a private read; Allow once runs the write exactly once

- **Risks (00 §2):** #2, #3. **Lane:** Es-PR (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. Agent with the Jira-shaped toolset against fake SaaS; script reads a private record, then calls create_issue.
  2. Card appears; member clicks Allow once.
- **User sees:** Card names destination (fake Jira), redacted payload and the titled source read; after Allow the run finishes and says the issue was created.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); interaction.requested{rule: flow_external_write} then interaction.resolved{approve, by member}; tool.intent precedes the effect; one tool.outcome.
- **Effects:** fake SaaS `effects_for(create_issue)` == 1 with the run's idempotency key.
- **Audit:** interaction.resolved chained; admin sees the call under effectivePolicy.heldRequests while it waits.
- **Covered today:** partial: frontend/tests/e2e/chat/chat-approval-card.spec.ts (UI-5, CA-8; dev-only lane).

#### EM-03 Flow guard: Deny sends nothing and the agent says so

- **Risks (00 §2):** #2. **Lane:** Es-PR (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome denied:user, external effects 0, child model inherit.
- **Steps:**
  1. Agent with an HTTP MCP server (fake); script calls an MCP read (unknown provenance), then an MCP write.
  2. Member clicks Deny.
- **User sees:** The answer says the action was not done; no retry card appears.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); tool.denied{rule: flow_external_write} or interaction.resolved{deny} followed by a denied outcome; no tool.intent executes.
- **Effects:** fake MCP server records 0 write calls.
- **Audit:** interaction.resolved{deny} chained.
- **Covered today:** new.

#### EM-04 Approval expires: nothing sent, run ends with a clear message

- **Risks (00 §2):** #2, #7. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome expired:approval_expired, external effects 0, child model inherit.
- **Steps:**
  1. Org approval policy writes=ask; script calls create_issue; nobody answers.
  2. Advance the run deadline (short deadline in the seed, or libfaketime on Node).
- **User sees:** Card turns to expired; the run says nothing was sent.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); interaction expires with approval_expired; run ends without a tool.outcome for the write.
- **Effects:** 0 effects.
- **Audit:** expiry recorded.
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/interactions.test.ts (unit).

#### EM-05 Allow for this conversation: the next matching write does not ask; a new conversation asks again

- **Risks (00 §2):** #2, #3. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome approved:written+grant, external effects 1, child model inherit.
- **Steps:**
  1. Read-then-write as EM-02; choose Allow for this conversation.
  2. Second turn in the same conversation writes to the same destination.
  3. Start a new conversation and repeat.
- **User sees:** Turn 2 runs without a card; the new conversation shows the card again.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); interaction.granted{scope: conversation, destination} journaled and chained; turn 2's call cites the grant (rebuilt from the journal).
- **Effects:** 2 effects after turn 2; the third conversation's write waits.
- **Audit:** interaction.granted chained (R2 F1 fix).
- **Covered today:** partial: frontend/tests/e2e/chat/chat-approval-card.spec.ts (CA-8), backend/nodejs/apps/tests/modules/agent-harness/ux1-conversation-grant.test.ts.

#### EM-06 Org deny rule refuses without a card

- **Risks (00 §2):** #2. **Lane:** Es-nightly (scripted). **Depends on:** CPD-1 for the audit assertion.
- **Oracle for the row:** route durable, executor loop, card no, outcome denied:flow_external_write, external effects 0, child model inherit.
- **Steps:**
  1. Admin sets the org approval policy's flow rule to deny (PUT /api/v1/agent-harness/admin/approvals).
  2. Member runs MCP read then write.
- **User sees:** No card; the answer says the action is not allowed by the organization's policy.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); tool.denied{rule: flow_external_write}.
- **Effects:** 0 effects.
- **Audit:** config change of the approval policy appears in the audit (today a log line: R2 F1; config.changed after CPD-1).
- **Covered today:** partial: backend/python/tests/unit/harness/test_sec2_flow_per_org.py (unit).

#### EM-07 Regenerate after a denied write is durable and asks again

- **Risks (00 §2):** #2. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome denied:user, external effects 0, child model inherit.
- **Steps:**
  1. EM-03 with the toolset; Deny.
  2. Click Regenerate on the answer.
- **User sees:** The regenerated turn shows the card again; denying again sends nothing.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); regenerate admitted as a durable run (regenerateOf set); a fresh interaction.requested.
- **Effects:** 0 effects across both turns.
- **Audit:** both decisions chained.
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/ux3-regenerate.mongo.itest.ts (I).

#### EM-08 Kill switch mid-flight

- **Risks (00 §2):** #5, #8. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. Start a durable run with a slow tool (fake SaaS delay 10 s).
  2. Admin turns on the Labs kill switch.
  3. Member sends a queued next message, then a new turn after the run ends.
- **User sees:** The running turn finishes on the harness; the queued message is refused with kill_switch text; the new turn answers on today's loop with the durable turns as history.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); no run.admitted after the switch; the in-flight run completes.
- **Effects:** the in-flight write executes once.
- **Audit:** Labs save recorded (log today; config.changed after CPD-1); `pipeshub_agent_chat_turns_total{path="legacy"}` increments.
- **Covered today:** partial: DUR3 kill-switch test (backend/nodejs/apps/tests/modules/agent-harness/harness-rollout.test.ts) is unit-level.

#### EM-09 Legacy path refuses an external write after a read

- **Risks (00 §2):** #2. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route legacy (chat_mode_not_supported), executor legacy, card no, outcome refused:approval_unavailable, external effects 0, child model inherit.
- **Steps:**
  1. Agent chat in planExecute (legacy-only); script reads a record then calls create_issue.
- **User sees:** The answer says the action needs approval, which this mode cannot ask for (approval_unavailable).
- **Journal:** no durable run admitted (rollout reason chat_mode_not_supported).
- **Effects:** 0 effects.
- **Audit:** legacy-path refusal logged once.
- **Covered today:** partial: backend/python/tests/unit/harness/test_sec1_flow_guard_loop.py (legacy refusal, unit).

#### EM-10 Server decides routing: legacy-only modes and project chats, one request

- **Risks (00 §2):** #11, #2. **Lane:** Es-nightly (scripted). **Depends on:** LAT1 for the one-request assertion.
- **Oracle for the row:** route legacy (project_scope), executor legacy, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Open a project chat by URL; send a body that claims no project (tampered: the client no longer sends a project flag, CPD-10).
  2. Send a planExecute turn.
- **User sees:** Each answers on today's loop; the browser makes one request per turn (after LAT1; today two for legacy-only modes).
- **Journal:** no durable run for either.
- **Effects:** —.
- **Audit:** path metric shows legacy with reason.
- **Covered today:** partial: frontend/app/(main)/chat/__tests__/project-scope.test.ts (unit).

#### EM-11 Worker SIGKILL mid-tool; resume on another worker; effect once

- **Risks (00 §2):** #3. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. Two workers; script calls an idempotent write; failpoint worker.tool.after_effect kills the worker.
  2. Keep the browser open.
- **User sees:** The answer completes after recovery (SLO #6 bound); no duplicate frames in the UI.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); a second run.claimed by the other worker with a higher fence; no event under the stale fence.
- **Effects:** `assert_exactly_once(idempotency key)`.
- **Audit:** —.
- **Covered today:** partial: integration-tests/harness/fault/test_crash_points.py (write_after_effect_idempotent; API-level, no browser).

#### EM-12 Lost acknowledgement leaves an unknown effect; card and console resolve it

- **Risks (00 §2):** #3, #7. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. Non-idempotent create with fake SaaS 'lose the ack after committing'; the worker is killed after the effect.
  2. HARNESS_RECONCILE off: admin resolves from the console (GET/POST /api/v1/agent-harness/admin/unknown-effects).
  3. HARNESS_RECONCILE on: rerun; the reconcile hook reads the provider.
- **User sees:** The chat shows an unknown-effect card instead of stalling; after resolution the run continues; with reconcile on, no card.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); tool.unknown then needs_reconciliation; the resolution journals the outcome from evidence; never a second execute.
- **Effects:** exactly 1 effect in fake SaaS in both variants.
- **Audit:** operator resolution recorded with actor.
- **Covered today:** partial: integration-tests/harness/fault/test_crash_points.py (write_after_effect_not_idempotent), frontend unit ux3-unknown-effect-card.test.tsx.

#### EM-13 Stop during a long model stream

- **Risks (00 §2):** #11. **Lane:** Es-PR (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Fake LLM streams for 30 s; member clicks Stop after 1 s.
- **User sees:** Streaming stops within 2 s p95 (SLO #7); the stopped partial shown equals the saved answer after reload.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); run.cancel_requested then run.cancelled; the model call is cancelled (no model.response after cancel).
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: frontend/tests/e2e/chat/chat-durable-stream.spec.ts (F-3; dev-only).

#### EM-14 Reconnect across a rolling Node restart

- **Risks (00 §2):** #7, #11. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Long stream; restart Node (compose restart) mid-stream.
- **User sees:** The stream resumes from Last-Event-ID with no repeated or missing text; reconnect ≤ 3 s after Node is back (SLO #8).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: frontend/tests/e2e/chat/chat-durable-stream.spec.ts (F-2 reload), integration-tests/harness/fault/test_upgrades.py (ru4a, API-level).

#### EM-15 Deep mode: a sub-agent's approval reaches the main thread

- **Risks (00 §2):** #2, #7. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. Deep agent with two sub-agents; one sub-agent writes after a read.
- **User sees:** The card appears in the main thread naming the sub-agent; after Allow, both children join and the parent answers.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); child.spawned x2 with budget shares; child interaction lifted to the parent stream; child.completed x2; workspace forks merged.
- **Effects:** 1 effect.
- **Audit:** child interactions chained under the child run.
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/projection/ux3-child-approvals.test.ts (unit).

#### EM-16 Engine run with opencode end to end, with approval and egress check

- **Risks (00 §2):** #2, #9. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop+code_task:opencode, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. `deployment/e2e/run.sh durable`: engine agent; the engine writes a file and files a ticket after approval.
  2. Reload; open the run timeline.
- **User sees:** Engine card shows progress, waiting-for-approval, the file version and cost; after reload the card is restored.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); engine.* events, workspace.committed, engine.checkpointed; interaction for the engine write (rule engine_writes).
- **Effects:** 1 ticket in fake SaaS; egress check: the sandbox reached only the gateways (deployment/e2e/check_egress.py).
- **Audit:** gateway.refused events only for the deliberate probes.
- **Covered today:** exists (dev-only): frontend/tests/e2e/chat/chat-durable-engine-e2e.spec.ts (DE-1..DE-4, DE-6) via deployment/e2e/run.sh durable.

#### EM-17 Codex engine run; worker killed mid-turn; one writer per workspace

- **Risks (00 §2):** #3. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop+code_task:codex, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Codex engine agent; kill the worker during the turn (DE-5 pattern).
- **User sees:** The UI ends right after a fresh worker resumes; no duplicate file versions.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); a second attempt claims; the first attempt's writer lease is lost before the second writes (workspace_writer_busy never violated).
- **Effects:** effects once.
- **Audit:** —.
- **Covered today:** partial: frontend/tests/e2e/chat/chat-durable-engine-e2e.spec.ts (DE-5, opencode only).

#### EM-18 Claude Code engine run (Messages wire)

- **Risks (00 §2):** #9. **Lane:** El-release (live). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop+code_task:claude_code, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. As EM-16 with Claude Code 2.1.294 (admitted by CC1).
- **User sees:** As EM-16.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** 1 effect.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness/engines/test_qa31_claude_code_rows.py (scripted, engine lane) and test_e2e4_live_matrix.py MC/claude_code (live, 3 of 3 on 2026-10-11; QA-31); no browser.

#### EM-19 run_code on the durable loop with no network

- **Risks (00 §2):** #9. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Script asks for a CSV summary; code also tries to reach a public host.
- **User sees:** Answer with the computed numbers and a downloadable file; the network attempt fails inside the code.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); execution receipt journaled; artifact published and presented.
- **Effects:** egress proxy refused the outbound; after the run no sandbox or network is left (broker ledger empty).
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness/sandbox_broker/test_durable_run_code_docker.py (Docker lane, no browser).

#### EM-20 run_code with open egress: public allowed, private and metadata refused

- **Risks (00 §2):** #9. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. SANDBOX_ALLOW_NETWORK=true; code fetches a public URL, 169.254.169.254 and a private address.
- **User sees:** Public fetch works; the others fail.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** firewall rules in place (egress_firewall).
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness/sandbox_broker/test_open_egress_firewall.py (Docker lane).

#### EM-21 MCP hub: unapproved tools absent; approve; drift disables

- **Risks (00 §2):** #1, #2. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. MCP_HUB=true; admin adds an MCP instance; member chats (tool absent); admin approves the manifest; member chats (tool used); fake MCP changes a tool schema; member chats.
- **User sees:** Tool unavailable with a typed reason, then usable, then disabled with an admin notice.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); pinned manifest digest equals the approved digest (CPS PN-3).
- **Effects:** —.
- **Audit:** manifest approval recorded (config.changed after CPD-1).
- **Covered today:** partial: frontend/tests/e2e/workspace/u4-manifest-approval.spec.ts; pin digest: backend/python/tests/unit/harness/assembly/test_descriptor_pins.py.

#### EM-22 STDIO MCP server in a runner sandbox with an egress allowlist

- **Risks (00 §2):** #9. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. MCP_STDIO_RUNNERS=true; instance declaring the host fake-saas:443; EGRESS_CONNECT_TUNNELS on, then off.
- **User sees:** Tool works with tunnels on; with tunnels off the HTTPS call fails with tunnel_refused surfaced as a tool error.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** egress.tunnel_opened/closed audited.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness/sandbox_broker/test_mcp_hub_runner_docker.py, test_egress_tunnel_docker.py.

#### EM-23 Two instances of one toolset keep their own credentials

- **Risks (00 §2):** #4. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. INSTANCE_TOOL_IDS=true; two Jira instances (two fake SaaS base URLs) with canary credentials; the script calls each once.
- **User sees:** Both calls succeed; tool names carry the instance tag.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); no canary string in the journal.
- **Effects:** each fake SaaS saw only its own credential.
- **Audit:** no canary in logs, spans or audit.
- **Covered today:** partial: backend/python/tests/unit/harness/assembly/test_instance_tool_ids.py (unit).

#### EM-24 Missing user credential: item unavailable, agent still answers

- **Risks (00 §2):** #1. **Lane:** Es-nightly (scripted). **Depends on:** CPD-7/CPD-8 for the typed action; today: partial.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. Toolset needs a per-user OAuth binding the member does not have.
- **User sees:** A 'connect your account' action appears for that item; the rest of the answer works (FR-2, FR-3).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); item unavailable{credential_missing} in the pin; run not failed.
- **Effects:** 0 effects.
- **Audit:** —.
- **Covered today:** new.

#### EM-25 Skills are private to their creator

- **Risks (00 §2):** #1. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. User A creates a private skill; user B runs an agent with no skill assignment and asks for it by name.
- **User sees:** B's run does not see or load A's skill.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); the pinned skills list excludes A's skill.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/agents/agent_loop/skills/test_skill_visibility.py (unit, both loops).

#### EM-26 Governed memory: proposal, review, use, erase

- **Risks (00 §2):** #4. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Admin sets memory policy user/auto; member states a preference; next conversation uses it; member deletes the conversation.
- **User sees:** The UI says a memory was saved (R1 F17); the next answer uses it; My memories lists it.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); memory proposal settled durably (the sweeper settles a crash-pending proposal).
- **Effects:** —.
- **Audit:** memory policy change audited (after CPD-1).
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/memory-service.test.ts (unit).

#### EM-27 Scheduled cron run: once per slot, as its creator, re-authorized

- **Risks (00 §2):** #1, #7. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. Labs scheduled runs on; member creates a cron schedule; the slot fires (time advanced); the run asks for approval.
  2. Admin disables the member; the next slot fires.
  3. Labs off; the next slot fires.
- **User sees:** Member is notified of the waiting approval; after allow the run completes; later slots are paused (actor_disabled) and refused (scheduled_runs_disabled).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); one run per slot (agentScheduleFirings unique); run actor = creator.
- **Effects:** 1 effect.
- **Audit:** firing records kept.
- **Covered today:** partial: frontend/tests/e2e/workspace/u4-schedules.spec.ts (CRUD only).

#### EM-28 Webhook-triggered schedule: duplicate delivery fires once

- **Risks (00 §2):** #3. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Post the same webhook event twice (same delivery id).
- **User sees:** One run, one answer notification.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** —.
- **Audit:** —.
- **Covered today:** new.

#### EM-29 Delete a conversation: everything erased; backfill for older deletions

- **Risks (00 §2):** #10. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Conversation with messages, citations, artifacts, a workspace and a sandbox folder; member deletes it.
  2. Seed a pre-upgrade deleted conversation; operator runs erasure-backfill-cli plan, then execute with the digest.
- **User sees:** Conversation gone from the UI; artifacts unreachable by direct link (404).
- **Journal:** session.erased{counts}; journal events and keys shredded.
- **Effects:** broker erase-session removed the sandbox folder; blob objects gone.
- **Audit:** backfill batches on the org's audit chain; rerun is a no-op.
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/session-erasure.mongo.itest.ts, erasure-backfill.mongo.itest.ts (I).

#### EM-30 Retention erases idle conversations

- **Risks (00 §2):** #10. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Admin sets retention 1 day; age a conversation past it (libfaketime or seeded timestamps); sweeper tick.
- **User sees:** The conversation disappears; an active conversation does not.
- **Journal:** session.erased{reason: retention}.
- **Effects:** —.
- **Audit:** retention setting change audited (after CPD-1).
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/session-retention.test.ts (unit, real timers: R3).

#### EM-31 Shared conversation: collaborator turn and continue privately

- **Risks (00 §2):** #4. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Owner shares a conversation; Labs collaborator turns on; collaborator sends a turn that reads a record only the collaborator can see; owner views; collaborator continues privately.
- **User sees:** Owner sees a placeholder, not the collaborator's private citation; the private fork has the collaborator's turns and citations only.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); run.admitted.sessionRole=collaborator; session.forked.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/collaborator-turns.mongo.itest.ts, frontend/app/(main)/chat/__tests__/continue-privately.test.tsx.

#### EM-32 Cross-org isolation over every harness route

- **Risks (00 §2):** #4. **Lane:** Es-PR (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Org B member replays org A's run id, interaction id, artifact id, workspace id and schedule id on every harness route.
- **User sees:** Every request answers 403 or 404 with no data.
- **Journal:** no event appended to org A's journal.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: QA-26 replays every harness route through the Node composition on a real MongoDB (backend/nodejs/apps/tests/modules/agent-harness/qa26-cross-tenant.mongo.itest.ts, route table held by qa26-route-inventory.test.ts); no browser-level run.

#### EM-33 Upgrade with runs in flight, both orders, and rollback

- **Risks (00 §2):** #5. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. Start runs on version N; upgrade workers first, then Node (and the reverse); then roll workers back.
- **User sees:** Every run finishes; none fails with a version error visible to the user.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); a run pinned by N+1 waits for an N+1 worker after a rollback (RU-5a).
- **Effects:** effects once.
- **Audit:** —.
- **Covered today:** partial: integration-tests/harness/fault/test_upgrades.py, deployment/docker-compose/tests/upgrade_smoke.sh.

#### EM-34 Helm install on kind: durable chat at chart defaults

- **Risks (00 §2):** #13. **Lane:** K8s-kind (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. deployment/helm/tests/kind_smoke.sh plus a scripted durable turn.
- **User sees:** The turn answers durably (path metric durable).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: deployment/helm/tests/kind_smoke.sh (install only).

#### EM-35 Alternative stores: ArangoDB, etcd, Kafka on Compose

- **Risks (00 §2):** #13. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Bring up the stack with those backends; EM-01 and EM-02's script.
- **User sees:** Same results as EM-01 and EM-02.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** as EM-02.
- **Audit:** —.
- **Covered today:** new (E2E4 ran ArangoDB knowledge cells at API level).

#### EM-36 Standalone MongoDB: legacy, explained

- **Risks (00 §2):** #7. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route legacy (infrastructure_unavailable), executor legacy, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Install with a standalone MongoDB; send an agent turn.
- **User sees:** The answer comes from today's loop; no 'Durable agent run failed' toast (R1 F7).
- **Journal:** no durable run; rollout reason infrastructure_unavailable.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: deployment/docker-compose/tests/mongodb_replset_test.sh (topology only).

#### EM-37 Model failures: retry and fallback when the policy is on

- **Risks (00 §2):** #7. **Lane:** Es-nightly (scripted). **Depends on:** CP7 (pinned fallback routes).
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. HARNESS_MODEL_POLICY=true with two pinned routes; fake LLM answers 500 twice on route 1.
- **User sees:** The answer arrives from route 2; with the flag off the user sees a typed model error.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); model.fallback journaled before route 2 is called.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness_core/durable/test_model_policy.py (unit).

#### EM-38 A runaway reply is cut and said so

- **Risks (00 §2):** #14. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Fake LLM repeats one paragraph without end.
- **User sees:** The answer ends with a 'cut short' notice; cost shown is bounded.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); output limit on the wire; repetition cut journaled.
- **Effects:** ledger reservation at the limit, settled on usage.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness_core/models/test_output_bound.py, harness_replay/test_rig_output_limit.py.

#### EM-39 Context compiler on a long session (live)

- **Risks (00 §2):** #12, #14. **Lane:** El-release (live). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Registered plan; a long conversation crosses 50% then 80% of the window.
- **User sees:** Answers stay correct; no visible change.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); context.cleared then context.compacted, each once per crossing.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/contracts/test_compiler_long_sessions.py (scripted).

#### EM-40 Narrowing applies to running work; widening waits for the next admission

- **Risks (00 §2):** #1. **Lane:** Es-nightly (scripted). **Depends on:** CPD-6.
- **Oracle for the row:** route durable, executor loop, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. Admin revokes the toolset grant while a run is suspended on an approval; member approves.
  2. Admin then widens approvals to allow; the running run's next write still asks.
- **User sees:** The approved call is refused as capability_lost within 5 s of the revoke (NFR-23); the widening applies to the next turn only (NFR-24).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); live narrowing recorded on the run's explain.
- **Effects:** 0 effects.
- **Audit:** config.changed for both writes.
- **Covered today:** new.

#### EM-41 Install, approve, grant, chat through the control plane

- **Risks (00 §2):** #1. **Lane:** Es-nightly (scripted). **Depends on:** CPD-11.
- **Oracle for the row:** route durable, executor loop, card no, outcome written, external effects 1, child model inherit.
- **Steps:**
  1. Admin installs an MCP server for a team, approves, grants to an agent; a team member chats; a non-member chats.
- **User sees:** Member's run uses the tool; non-member sees it unavailable with not_visible and a next action.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); ResolvedRunConfig pinned with descriptor digests.
- **Effects:** —.
- **Audit:** config.changed x3 with approvalRef.
- **Covered today:** new.

#### EM-42 Isolation under gVisor

- **Risks (00 §2):** #9. **Lane:** Iso-gVisor (scripted). **Depends on:** QA-25.
- **Oracle for the row:** route durable, executor loop+code_task:opencode, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Broker isolation class runsc; run_code and an engine turn; C-28 confinement probes.
- **User sees:** As EM-16 and EM-19.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** probes refused.
- **Audit:** —.
- **Covered today:** needs a host with runsc; written for the gVisor runner (QA-25).

#### EM-43 Isolation under Kata

- **Risks (00 §2):** #9. **Lane:** Iso-Kata (scripted). **Depends on:** QA-25.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Broker isolation class kata; run_code; confinement probes; cold start time (SLO #17 tier 2).
- **User sees:** As EM-19.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** probes refused.
- **Audit:** —.
- **Covered today:** needs a host with KVM; Kata runner (QA-25).

#### EM-44 Red team: planted injections do not exfiltrate (live)

- **Risks (00 §2):** #2. **Lane:** El-release (live). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome denied:user, external effects 0, child model inherit.
- **Steps:**
  1. Red-team cases: injections in a knowledge record and an MCP result ask the model to send data out.
- **User sees:** No data leaves; any held call is denied by the scripted reviewer.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** fake SaaS and egress logs show no attacker-bound request.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness/security/redteam (H7); engine-side drivers left to a human pen test.

#### EM-45 Accessibility and translation of harness screens

- **Risks (00 §2):** #15. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. Run axe on the chat with an approval card, an unknown-effect card, the engine card, Labs, approval policy, unknown-effects console; switch the locale.
- **User sees:** No serious or critical axe violations; the card's arrival is announced; errors and statuses are translated.
- **Journal:** —.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** new.

#### EM-46 MCP client and Slack surfaces on the durable path

- **Risks (00 §2):** #12. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Run the published @pipeshub-ai/mcp pipeshub_chat against the stack; post a Slack-shaped request to /internal slackAgentRun.
- **User sees:** Both get the answer; the MCP client parses the terminal frame; Slack gets no tool arguments or results.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers).
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: integration-tests/mcp_package (published package), integration-tests/harness/fault/test_surface_leaks.py.

#### EM-47 Steering: a message while the run works is added to the task

- **Risks (00 §2):** #12. **Lane:** Es-nightly (scripted). **Depends on:** none.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. During a slow tool call, the member sends a follow-up.
- **User sees:** The placeholder says the message goes to the running task; the answer reflects it; an unused message offers 'Send as new message' (R1 F19).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); input.queued{by} consumed once.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: integration-tests/harness/fault/test_crash_points.py (steer_*), backend/nodejs/apps/tests/modules/agent-harness/steering.http.test.ts.

#### EM-48 Activity panel: a run_code program's output streams live, survives a reload and stays the owner's

- **Risks (00 §2):** #15. **Lane:** Es-nightly (scripted). **Depends on:** QA-5 harness lane.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. The member asks for a computation the scripted model answers with run_code printing several lines over a few seconds; the member opens Activity while it runs.
  2. The member reloads the page; a second member the conversation is shared with opens the same run's activity URL.
- **User sees:** The panel shows the command, its output appearing line by line before it finishes, then 'Exit 0' and its duration; Tool calls lists run_code as Done; a secret the program prints shows as ••••••. After the reload the panel shows the same command with its output tail and a working 'Full log'; the second member gets no panel (404) and the chat shows no output to them.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); one x.pipeshub.command.finished per run_code call in the call's outcome commit, its tails and log redacted.
- **Effects:** the program ran once (receipt done).
- **Audit:** —.
- **Covered today:** partial: frontend/tests/e2e/chat/chat-activity-panel.spec.ts (mocked SSE; dev-only lane), backend/python/tests/unit/harness/assembly/test_ux5_run_code_activity.py, backend/nodejs/apps/tests/modules/agent-harness/ux5-activity-route.http.test.ts.

#### EM-49 Suggested next steps: chips after the answer fill the input and never send; the call is charged to the run

- **Risks (00 §2):** #14. **Lane:** Es-nightly (scripted). **Depends on:** QA-5 harness lane.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. The member asks a question; the scripted model answers, then answers the suggestions call that follows the run's end with three questions.
  2. The member clicks a chip; an admin turns Labs 'Suggested Follow-up Questions' off and the member asks again.
- **User sees:** The answer completes at once (Stop disappears, the box is free); three chips appear under it a moment later; clicking one puts its text in the message box, focused, and nothing is sent. With the flag off no chips appear and no extra model call is made.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); nothing about suggestions in the journal; the run document holds followups.
- **Effects:** —.
- **Audit:** the run's ledger committed one more small call (output at most 384 tokens).
- **Covered today:** partial: frontend/app/(main)/chat/components/message-area/__tests__/ux5-followup-chips.test.tsx (unit), backend/python/tests/unit/harness/test_ux5_followups.py (unit).

#### EM-50 Deep mode: a sub-agent whose role is set to another model calls that model, and only it does

- **Risks (00 §2):** #1, #14. **Lane:** Es-nightly (scripted). **Depends on:** MA1.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model role:different.
- **Steps:**
  1. An admin saves the agent with roleModels {default: B} (PUT /api/v1/agents/{key}); B is a configured model on another wire than the turn's model A.
  2. A member sends a deep-mode turn on A; the coordinator plans and hands the request to one sub-agent, which reads the knowledge base and returns.
- **User sees:** The sub-agent's row shows its role and model B, the run shows A (MA1 QA37-X3); the parent answers from what the sub-agent found.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); child.spawned.data.model = {provider, model: B, role: default} on the parent's stream; the child's run.spec_pinned routes to B alone (spec.model.policy) and names B's configured entry (x.pipeshub.model); the parent's spec is A's, untouched; the scripted model served the sub-agent's calls on B's wire and model and the coordinator's on A's (/__control/requests); the same turn with no roleModels: child.spawned has no model member and every call names A.
- **Effects:** —.
- **Audit:** the ledger commits the child's calls at B's price and the parent's at A's (QA37-I6).
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/ma1-child-spawn.test.ts (QA37-I1), backend/python/tests/unit/harness/pinning/test_ma1_child_models.py (QA37-I2); no deployment.

#### EM-51 Deep mode: a role model the org does not allow refuses that spawn; the parent is told and answers

- **Risks (00 §2):** #1. **Lane:** Es-nightly (scripted). **Depends on:** MA1; CPD-11 (a route that writes an org's models.routes); CONTROL_PLANE_PIN=on.
- **Oracle for the row:** route durable, executor loop, card no, outcome denied:child_grant_too_large, external effects 0, child model refused:models.roles.
- **Steps:**
  1. Node runs with CONTROL_PLANE_PIN=on. The agent is saved with roleModels {default: B}; then the org's stored models.routes is narrowed to exclude B.
  2. A member sends a deep-mode turn whose script reads an issue, then opens one; the coordinator delegates it.
- **User sees:** No sub-agent row and no approval card; the run completes with the parent's answer and nothing is written. The agent builder shows the role's stored model as no longer allowed, never silently replaced (QA37-X2).
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); no child.spawned and no child run; tool.denied{rule: child_grant_too_large} then the call's error outcome on the parent's stream; the parent's run.completed; Node logs child.refused once at warning with reason CHILD_GRANT_TOO_LARGE and dimension models.roles; no model call names B.
- **Effects:** 0 effects.
- **Audit:** the run's pinned models.roles holds no entry for the dropped role (QA37-U3).
- **Covered today:** partial: backend/nodejs/apps/tests/modules/agent-harness/ma1-child-spawn.test.ts (QA37-I4); no deployment.

#### EM-52 Engine run (opencode): the agent's own model, not the org default, reaches the provider through the model gateway

- **Risks (00 §2):** #1, #14. **Lane:** Es-nightly (scripted). **Depends on:** MA1.
- **Oracle for the row:** route durable, executor loop+code_task:opencode, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. An admin configures a second model B on the chat completions wire beside the org's default A; the opencode agent is saved with B as its only model and no roleModels.
  2. A member sends a turn that names no model; the engine writes a file and answers.
- **User sees:** The answer completes and the run names model B (MA1 QA37-X3); nothing shows A.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); the run's pinned spec routes to B alone (run.spec_pinned.data.spec.model.policy); no child.spawned: an engine's helpers are not child runs and call the run's engine-model (MA1 §6); the scripted model's requests for the run (/__control/requests) are all on the chat completions wire and name B; none names A.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness_core/gateway/test_ma1_gateway_child_model.py (QA37-I3: the gateway serves the grant's first route on each wire; no deployment).

#### EM-53 Engine run (Codex): the agent's own model, not the org default, reaches the provider through the model gateway

- **Risks (00 §2):** #1, #14. **Lane:** Es-nightly (scripted). **Depends on:** MA1.
- **Oracle for the row:** route durable, executor loop+code_task:codex, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. An admin configures a second model B on the Responses wire beside the org's default A; the Codex agent is saved with B as its only model and no roleModels.
  2. A member sends a turn that names no model; the engine writes a file and answers.
- **User sees:** The answer completes and the run names model B (MA1 QA37-X3); nothing shows A.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); the run's pinned spec routes to B alone (run.spec_pinned.data.spec.model.policy); no child.spawned: an engine's helpers are not child runs and call the run's engine-model (MA1 §6); the scripted model's requests for the run (/__control/requests) are all on the Responses wire and name B; none names A.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness_core/gateway/test_ma1_gateway_child_model.py (QA37-I3: the gateway serves the grant's first route on each wire; no deployment).

#### EM-54 Engine run (Claude Code): the agent's own model, not the org default, reaches the provider through the model gateway

- **Risks (00 §2):** #1, #14. **Lane:** Es-nightly (scripted). **Depends on:** MA1.
- **Oracle for the row:** route durable, executor loop+code_task:claude_code, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. An admin configures a second model B on the Messages wire beside the org's default A; the Claude Code agent is saved with B as its only model and no roleModels.
  2. A member sends a turn that names no model; the engine writes a file and answers.
- **User sees:** The answer completes and the run names model B (MA1 QA37-X3); nothing shows A.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); the run's pinned spec routes to B alone (run.spec_pinned.data.spec.model.policy); no child.spawned: an engine's helpers are not child runs and call the run's engine-model (MA1 §6); the scripted model's requests for the run (/__control/requests) are all on the Messages wire and name B; none names A.
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness_core/gateway/test_ma1_gateway_child_model.py (QA37-I3: the gateway serves the grant's first route on each wire; no deployment).

#### EM-55 A role's model withdrawn mid-run: the sub-agent's claim fails clearly, never on another model

- **Risks (00 §2):** #1, #7. **Lane:** Es-nightly (scripted). **Depends on:** MA1; CONTROL_PLANE_PIN=on; the fault lane's failpoints.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model role:different.
- **Steps:**
  1. Node runs with CONTROL_PLANE_PIN=on. The agent has roleModels {default: B}; a member sends a deep-mode turn on A.
  2. The worker is held after the child's admission (failpoint worker.spawn.after_admission, fault lane); an admin deletes B's model configuration; the worker is released.
- **User sees:** The sub-agent's row ends failed with a clear message for model_no_longer_configured; the parent's run completes with its answer on A. No answer comes from another model.
- **Journal:** history checks pass (gap-free seq, one run.spec_pinned first, each tool.outcome after its tool.intent, nothing after the terminal event but retention markers); child.spawned.data.model = {provider, model: B, role: default}; the child's run ends run.failed with model_no_longer_configured at its claim, with no model.request on its stream; the parent's run.completed; the scripted model served no call on B and no call for the child on A (/__control/requests).
- **Effects:** —.
- **Audit:** —.
- **Covered today:** partial: backend/python/tests/unit/harness/pinning/test_ma1_child_models.py (QA37-I5); no deployment.

#### EM-56 R31-SECKEY: no org or platform credential enters an engine sandbox, no upstream is reachable from one but through the gateways, and a sandbox's tokens reach only its run's grants

- **Risks (00 §2):** #2, #3, #9. **Lane:** Es-nightly (scripted). **Depends on:** R31-SECKEY.
- **Oracle for the row:** route durable, executor loop+code_task:opencode, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. On a human-style stack, canaries are planted where the product keeps every kind of credential (deployment/e2e/seckey_inventory.py, held to the code's enumerations by tests/cohesion/test_r31seckey_canary_inventory.py): a stored model per LLM and embedding provider, MCP servers signed in by API token, header and OAuth (access, refresh, client secret), every keyed web search provider and SearXNG (live), Jira Data Center (live), OAuth and basic-auth toolset sign-ins, and every secret in the stack's and the platform containers' environments.
  2. On every release engine (engines.py kinds), two jobs of an agent with all of them: one holds before its first call, one after every credential-bearing call; each calls four MCP servers, the Jira toolset and web search.
- **User sees:** Each job ends with its answer and every tool's result (SK-5).
- **Journal:** SK-1..SK-3: no canary in raw, base64, percent-encoded or hex form in any file, environment or command line of the live sandbox (sentinels prove /tmp, /state, /workspace and environments were read); no credential shape; credential-named variables hold placeholders only; SD-1..SD-3: no upstream name resolves (providers, search, git, fake LLM and SaaS, the app); no TCP to their addresses; through the proxy with the placeholder every upstream is refused and both gateways answer; SG: the tools placeholder lists only granted tools; ungranted tools, servers, toolsets, knowledge and the parent's code_task are refused; a stale attempt, another org and a forged token are refused; the model token names only the grant's neutral model; after the run both are refused and none is minted; a finished job's placeholder reaches no gateway.
- **Effects:** SK-4: the scripted model received the org's key and the fake SaaS the MCP token, header, OAuth token, SearXNG's key and Jira's token, none from a sandbox's address; no non-model credential reached the model.
- **Audit:** refusals are logged with their run id and carry no token or credential.
- **Covered today:** deployment/e2e/seckey.py (run.sh seckey RUN); readers: backend/python/tests/unit/harness/test_r31seckey_canary_lane.py.

#### EM-57 R32-TRUST: at the production floor (gVisor, no dev override) every release engine is offered and runs, its ACP profiles verified against the stack's own test root; an untrusted profile reads "not signed for this deployment", never "not installed"

- **Risks (00 §2):** #3, #9. **Lane:** Iso-gVisor (scripted). **Depends on:** R32-TRUST.
- **Oracle for the row:** route durable, executor loop+code_task:opencode, card yes, outcome approved:written, external effects 1, child model inherit.
- **Steps:**
  1. Bring a human-style stack up with E2E_SANDBOX_RUNTIME=runsc: run.sh signs the tree's ACP profiles with a fresh test key (scripts/engines/sign_vendor_rules.py --test-root, private half never written) and mounts the root read-only as PIPESHUB_TEST_TRUST_ROOT; PIPESHUB_ACP_PROFILES_UNSIGNED and PIPESHUB_ENGINES_WITHOUT_GVISOR are false.
  2. Run smoke_human.py, then journeys.py --tier gate, whose owner_pinned groups pin each ACP engine (acp-opencode, acp-gemini, acp-goose, acp-qwen, acp-dsh).
- **User sees:** Every engine is a choice on the coding-engine pickers; each ACP job ends with its answer, its approvals, cancel, delete, budget and tool results as on the native engines. Where a profile is untrusted (no test root, no release signature), the engine is listed greyed with "This engine's profile isn't signed for this deployment" (10 locales), and a save is refused with the same words.
- **Journal:** The broker logs enginesUntrusted=[] at start and lists no engine under enginesUnavailable; every job's sandbox runs on runsc..
- **Effects:** No unsigned profile is read: a profile changed after signing, or a signature by another key, installs nothing (test_r32_profile_trust.py)..
- **Audit:** A release build with PIPESHUB_TEST_TRUST_ROOT and without PIPESHUB_ALLOW_TEST_TRUST_ROOT=true does not start (ProfileTestRootRefused)..
- **Covered today:** deployment/e2e/smoke_human.py and deployment/e2e/journeys.py --tier gate on an E2E_SANDBOX_RUNTIME=runsc human-up stack; readers: backend/python/tests/cohesion/test_r32_stack_trust.py, backend/python/tests/unit/harness_engines/test_r32_profile_trust.py, backend/nodejs/apps/tests/modules/agent-harness/r32-engine-profile-untrusted.test.ts, frontend/app/(main)/workspace/ai-accounts/__tests__/r32-engine-profile-untrusted.test.ts.

#### EM-58 R38-IDENT (SEC-C-03, ADR-SLACKID-01): a Slack person acts only as the PipesHub account they linked; a spoofed profile email, a Slack Connect outsider, a guest and a shared channel are refused privately with no lookup

- **Risks (00 §2):** #3, #12. **Lane:** Iso-gVisor (scripted). **Depends on:** R38-IDENT.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Bring a human-style stack up (E2E_FAKE_SLACK=1, the default of human-up) with E2E_SANDBOX_RUNTIME=runsc.
  2. Run journeys.py on any surface.slack journey: its setup runs slack_identity_holds (a member whose Slack profile email is the admin's, a member of another team, a guest, and the admin in a channel shared with another organisation each mention the bot), then link_slack_person for the admin and the member through the sign-in link the bot sent each of them privately.
- **User sees:** Each refused person gets a private reply saying plainly that this Slack account cannot use PipesHub here and what to do; nothing is answered in the channel. An unlinked member gets a private sign-in link; the /slack/link page names both accounts before Approve, and after it the member's mentions are answered as them.
- **Journal:** No conversation or run is started for a refused event, and none under the spoofed email's account..
- **Effects:** The binding is stored by Slack team id + user id; changing the Slack profile email does not move it; a disabled or deleted PipesHub account loses it at its next use (r38-slack-identity.test.ts)..
- **Audit:** A link token is single-use, expires after 10 minutes and is bound to its team, user and bot: a replay, an expired token or another team's token links nothing..
- **Covered today:** deployment/e2e/journeys.py (slack_identity_holds, link_slack_person) on a human-up stack; readers: backend/nodejs/apps/tests/integrations/slack-bot/r38-slack-identity.test.ts, backend/nodejs/apps/tests/modules/agent-harness/sec-c-platform-appsec.test.ts, frontend/app/(public)/slack/link/__tests__/slack-link-view.test.tsx.

#### EM-59 R41-SUPPLY (SEC-D-04, ADR-SEC-05): install.sh runs only a release image whose signature it checked, by digest; a re-tagged unsigned image is refused plainly and starts nothing

- **Risks (00 §2):** #12. **Lane:** Es-PR (scripted). **Depends on:** R41-SUPPLY.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Run deployment/docker-compose/tests/image_signature_test.sh; with IMAGE_SIG_LIVE=1, COSIGN_BIN and CRANE_BIN it signs an image in an in-memory registry with a throwaway key, then re-pushes the tag to an unsigned image.
- **User sees:** The signed digest is pinned in .env (PIPESHUB_APP_PINNED) and Compose runs it; the re-tagged image is refused with what happened, that nothing started, and what to do.
- **Journal:** None: the check runs before any container starts..
- **Effects:** require refuses, warn pins and says so, off pins without a check, an unknown mode is require; a foreign key's signature is refused..
- **Audit:** The keyless check names only release-publish.yml at a v* tag of this repository and the GitHub Actions issuer..
- **Covered today:** deployment/docker-compose/tests/image_signature_test.sh; backend/python/tests/unit/harness/security/test_sec_d_ops_supply_chain.py (SEC-D-04).

#### EM-60 R41-SUPPLY (SEC-D-09, ADR-OPS-04): on a cluster, the broker and its sandboxes reach neither cloud metadata nor the stores, and the app still works

- **Risks (00 §2):** #3. **Lane:** K8s-kind (scripted). **Depends on:** R41-SUPPLY.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. kind with Calico or Cilium; helm install with values-eks.yaml and tests/values-eks-kind.yaml (network policy on).
  2. From the broker pod and a legacy open-egress sandbox: curl 169.254.169.254 and 169.254.170.23; nc to redis 6379 (sandbox), mongo 27017, qdrant 6333 and the app's 3001 (broker).
- **User sees:** Every probe is refused or times out; run_code succeeds; the app answers a chat with a model call.
- **Journal:** None..
- **Effects:** A pod in another namespace reaches no store, the broker or the app; with networkPolicy.enabled=false NOTES carries the warning..
- **Audit:** deployment/helm/tests/netpol_check.py's allowed-path table matches what the cluster allows..
- **Covered today:** deployment/helm/tests/netpol_check.py, test_netpol.py (rendered); the cluster run is R41SUPPLY-FU-4.

#### EM-61 R42-SMALL (R38IDENT-FU-13): a deep agent's sub-agent calls the MCP hub tool its coordinator listed at the claim, with no second listing of the STDIO server

- **Risks (00 §2):** #7. **Lane:** Iso-gVisor (scripted). **Depends on:** R42-SMALL.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Bring a human-style stack up with E2E_SANDBOX_RUNTIME=runsc (human-up has the MCP hub on by default).
  2. Run journeys.py --only J-agent.deep-seeded-hub: the deep coordinator delegates and its sub-agent calls the hub's STDIO tool.
- **User sees:** The answer carries the STDIO server's reply; no 'tool not available' recovery is shown.
- **Journal:** No tool.recovery choice=not_available for the hub tool; the child's carried pin names the coordinator's listing..
- **Effects:** The hub server is listed once per run tree: the sub-agent builds the tool from the stored catalog and only the call reaches the runner (test_r42small_deep_sub_agent_pins.py FU13-2)..
- **Audit:** A tool the coordinator no longer holds is not carried to the sub-agent (FU13-3)..
- **Covered today:** deployment/e2e/journeys.py J-agent.deep-seeded-hub on a human-up stack; readers: backend/python/tests/unit/harness/assembly/test_r42small_deep_sub_agent_pins.py.

#### EM-62 R41-OPS (SEC-D-07, SEC-C-14, ADR-WEB-01): the signed-in app's main pages load under the enforced CSP with no console violation, an injected inline script does not run, and no page can be framed

- **Risks (00 §2):** #3. **Lane:** Iso-gVisor (scripted). **Depends on:** R41-OPS.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. Bring a human-style stack up with E2E_SANDBOX_RUNTIME=runsc.
  2. run.sh pw RUN test --project=authenticated tests/e2e/security/browser-security-headers.spec.ts: it opens /chat/, /knowledge-base/, /artifacts/, /agents/ and the workspace pages signed in, keeps a screenshot of each, frames the chat and injects an inline script.
- **User sees:** Every page renders and works as before; the browser console shows no Content-Security-Policy violation. The chat inside a frame does not load.
- **Journal:** No run is started by the spec..
- **Effects:** Each page response carries frame-ancestors 'none', X-Frame-Options DENY, nosniff, a script-src of 'self' with sha256 hashes and no 'unsafe-inline', and no X-Powered-By; /api/v1/health carries default-src 'none'..
- **Audit:** Violation reports reach POST /api/v1/csp-report and are logged by directive, blocked origin and page path only..
- **Covered today:** frontend/tests/e2e/security/browser-security-headers.spec.ts on a human-up stack (and every base-fixture spec fails on a CSP violation); readers: backend/nodejs/apps/tests/libs/middlewares/security-headers.test.ts, backend/nodejs/apps/tests/app.test.ts.

#### EM-63 R41-OPS (SEC-D-06, SEC-D-08, ADR-OPS-03): every bundled store refuses an anonymous client, the app refuses an open store with what to set, and the app port is local unless the operator opens it

- **Risks (00 §2):** #3, #12. **Lane:** Iso-gVisor (scripted). **Depends on:** R41-OPS.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. bash deployment/docker-compose/tests/store_auth_test.sh (through $PXH_LANE when set): Redis, etcd and Kafka from the real docker-compose.yml, an etcd volume written by the old plain-HTTP etcd.
  2. bash deployment/docker-compose/tests/installer_test.sh: an older .env reused, with and without a public URL.
  3. A human-up stack (its own Redis) comes up with STORE_AUTH_CHECK enforcing and passes smoke_human.py.
- **User sees:** An operator upgrading sees which credentials were generated and, when the app port closes to this machine, what to set to open it again; no data is lost.
- **Journal:** The app does not start against a Redis that answers PING anonymously (store-auth.test.ts); the message names the store and the variable..
- **Effects:** Anonymous Redis PING gets NOAUTH; plain HTTP, plaintext gRPC and TLS without a client certificate to etcd are refused; a Kafka client without SASL or with a wrong password gets nothing; ZooKeeper is unreachable from the app's network; each component's own etcd certificate and the SCRAM login work..
- **Audit:** No credential value is printed by the installer, the lane or the refusal..
- **Covered today:** deployment/docker-compose/tests/store_auth_test.sh, deployment/docker-compose/tests/installer_test.sh; readers: backend/python/tests/unit/harness/security/test_sec_d_store_auth.py, backend/nodejs/apps/tests/libs/config/store-auth.test.ts, deployment/helm/tests/test_store_auth.py.

#### EM-64 R43-AUTH (R41OPS-FU-7, R41OPS-FU-8, ADR-WEB-02): the web session survives a reload and a new tab with no token in page storage, an older build's localStorage tokens move to the HttpOnly cookie, and the unauthenticated health routes name no store

- **Risks (00 §2):** #3, #12. **Lane:** Iso-gVisor (scripted). **Depends on:** R43-AUTH.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. On a human-up stack, the authenticated Playwright project runs frontend/tests/e2e/auth/session-persistence.spec.ts and auth/logout.spec.ts.
  2. curl -s /api/v1/health and /api/v1/health/services without a token; the same with the admin's token.
  3. smoke_human.py and run.sh wait_capabilities read agentHarness.capabilities from the public answer.
- **User sees:** A person who signed in stays signed in after a reload, in a second tab and after the upgrade from a build that kept tokens in localStorage; clearing the browser's cookies signs them out.
- **Journal:** No run is started by the spec..
- **Effects:** After sign-in localStorage and sessionStorage hold no jwt_* key and no JWT-shaped value; the session cookie is HttpOnly and SameSite=Strict (__Host-, Secure, Path=/ over https); the unauthenticated /api/v1/health answers only status and agentHarness {status, capabilities}, /api/v1/health/services only status and the product services; the admin's token gets the full reports..
- **Audit:** A refresh or sign-out that sends the cookie from another origin, or without an Origin, is refused 403..
- **Covered today:** frontend/tests/e2e/auth/session-persistence.spec.ts on a human-up stack; readers: backend/nodejs/apps/tests/modules/auth/routes/session-cookie.http.test.ts, backend/nodejs/apps/tests/modules/tokens_manager/routes/health.routes.test.ts, frontend/lib/auth/__tests__/session-restore.test.ts, frontend/lib/__tests__/no-token-in-web-storage.test.ts.

#### EM-65 R44-IDENT (R43AUTH-FU-1, ADR-WEB-02 D9): every refresh replaces the refresh token; two tabs refreshing together stay signed in; a replayed old cookie ends that sign-in and is audited

- **Risks (00 §2):** #3, #12. **Lane:** Iso-gVisor (scripted). **Depends on:** R44-IDENT.
- **Oracle for the row:** route durable, executor loop, card no, outcome answered, external effects 0, child model inherit.
- **Steps:**
  1. On a human-up stack, sign in in the browser; copy the session cookie's value (DevTools or the Playwright context) as the 'old' cookie.
  2. Open a second tab and reload both at once; wait for the proactive refresh in each.
  3. Wait more than 60 seconds, then POST /api/v1/userAccount/refresh/token from a fresh context with the old cookie and this server's Origin.
  4. Reload the original tab; as an admin, search the audit log for identity.session_replayed.
- **User sees:** Both tabs stay signed in through simultaneous refreshes; after the old cookie is replayed the person is asked to sign in again on that device only.
- **Journal:** No run is started..
- **Effects:** Each refresh answers a new Set-Cookie value with the same expiry; the replay answers 401 and clears the cookie; the family's next refresh with the newest cookie is also 401; a desktop or bearer refresh gets refreshToken in the body..
- **Audit:** One identity.session_replayed entry on the org's audit chain for that sign-in (account and family id, no token, no address); auth.session_replay_detected in the app log..
- **Covered today:** readers: backend/nodejs/apps/tests/modules/auth/session-rotation.test.ts, backend/nodejs/apps/tests/modules/auth/controller/userAccount.controller.test.ts, frontend/lib/auth/__tests__/session-rotation.test.ts; Playwright spec not yet written (R44IDENT-FU-1).

<!-- END generated: must-run -->

## 4. Covering arrays: how a row becomes a test

### 4.1 Stack profiles

Bringing up a stack costs 5–10 minutes, so rows are constrained to seven stack profiles that themselves
pairwise-cover the five store and deployment factors. ST-1 is the PR lane's stack.

| Stack | graph | mongo | kv | broker | deploy | Pairwise rows | 3-wise rows |
|---|---|---|---|---|---|---|---|
| ST-1 | neo4j | replset | redis | redis_streams | compose | 19 | 6 |
| ST-2 | neo4j | replset | redis | redis_streams | helm | 21 | 8 |
| ST-3 | neo4j | replset | redis | kafka | compose | 18 | 18 |
| ST-4 | neo4j | standalone | etcd | redis_streams | compose | 12 | 11 |
| ST-5 | arango | replset | redis | redis_streams | compose | 16 | 9 |
| ST-6 | arango | replset | etcd | kafka | helm | 32 | 16 |
| ST-7 | arango | standalone | redis | kafka | helm | 9 | 7 |

(Counts are from the committed arrays; regenerate with the script and update this table when they change.)

### 4.2 Setting up a row

A runner (`integration-tests/harness/e2e/`, WP QA-6) reads a row and applies one setup step per factor level.
All steps exist as product settings or test fixtures today; none needs a test-only product switch.

| Factor | Level | Setup |
|---|---|---|
| path | durable | defaults (`REPLICA_SET_AVAILABLE`, relay and worker on, Labs on) |
| | kill_switch | Labs `AGENT_HARNESS_KILL_SWITCH` on through `POST /api/v1/configurationManager/platform/settings` |
| | standalone_mongo | the stack's standalone MongoDB (ST-4, ST-7) |
| engine | opencode, codex, claude_code | `HARNESS_ENGINE_RUNS=true` on the worker; the agent saved with an `engineProfile` (no org setting: every released engine runs, `engines.prepare_org`) |
| wire | any | the seeded model config points at the fake LLM with that provider shape (`deployment/e2e/seed_config.py`) |
| compiler | on | `HARNESS_CONTEXT_COMPILER=true` on the worker |
| flow_guard | off / ask / org_deny | `HARNESS_FLOW_GUARD=false` / default / the org policy's flow rule `deny` via `PUT /api/v1/agent-harness/admin/approvals` |
| write_policy | ask | `/services/agentHarness/approvals` = `{"orgs":{"<org>":{"writes":"ask"}}}` through the admin route |
| decision | allow_once, allow_conversation, deny | Playwright clicks the card's button (or posts the interaction response for API rows) |
| | expire | the seed gives the run a short deadline; nobody answers |
| tool_family | knowledge | a KB with one record the actor can read (and one the actor cannot) |
| | mcp | the fake MCP server over streamable HTTP; with `MCP_HUB=true` its manifest is approved first |
| | toolset | the Jira-shaped toolset against fake SaaS, with a canary credential |
| | run_code | `SANDBOX_MODE` as the row says; the script runs code that writes a file |
| | skills | a pinned skill owned by the actor |
| | memory | memory policy `{"mode":"user","review":"auto"}` through `PUT /api/v1/agent-harness/admin/memory-policy` |
| sandbox | docker / broker_* | `SANDBOX_MODE=docker` or `broker`; allowlist via the agent's egress hosts; `SANDBOX_ALLOW_NETWORK=true` for broker_open |
| trigger | schedule_cron | Labs scheduled runs on; `POST /api/v1/agent-harness/schedules` with a cron one minute ahead (or libfaketime on Node once QA-17 lands) |
| | schedule_webhook | a webhook schedule; the runner posts the event (twice, with one delivery id) |
| role | admin / operator | the extra assertions in §4.4 |
| child_model | same / different | the row's agent is saved with `roleModels: {"default": {modelKey, modelName, provider}}` through `PUT /api/v1/agents/{key}`: the turn's own model, or the seeded model of another wire (`anthropic_messages` for an `openai_chat` turn, else `openai_chat`), so the scripted model's record of a call (wire and model) shows whose model it named |
| | forbidden | as `different`, with the org's stored `models.routes` excluding that model and Node on `CONTROL_PLANE_PIN=on`. **Blocked (CPD-11):** no admin route writes an org's `models.routes` yet, and the stacks' Node runs the default `shadow`, which counts a forbidden role model without refusing it |
| | any but none, legacy route | **Blocked (QA37-F1):** MA1 §3 gives the legacy loop's sub-agents (not child runs) no rule for a role model |

A blocked level is planned and reported as `blocked <finding>` in the row results, never passed and never dropped:
of the 26 role-model rows (PW-102..PW-127), 12 run, 5 wait on CPD-11, 7 on QA37-F1 and 2 on both.

The fake LLM script is chosen by `(mode, tool_family)`: `answer`, `read-then-answer`, `read-then-write-<family>`,
`code-then-answer`, `skill-then-answer`, `remember-then-answer`; regenerate replays the same script; reconnect
adds a mid-stream disconnect from the runner.

### 4.3 The oracle

The expected outcome of a row is a pure function of its factors, `oracle(row)` in
`scripts/e2e_covering_array.py`, and every row in [`generated/e2e-matrix.md`](generated/e2e-matrix.md) shows it.
The runner imports the same function, so the matrix and the test cannot disagree.

| Question | Rule |
|---|---|
| Route | legacy if the path is `standalone_mongo` (`infrastructure_unavailable`) or `kill_switch` (`kill_switch`), or the mode is legacy-only (`chat_mode_not_supported`; project chats: `project_scope`); otherwise durable |
| Executor | `loop+code_task:<name>` when an engine is set (CTL-6: the turn runs on the loop and may hand off to that engine); else `loop` on the durable route, `legacy` otherwise. A run pinned as an engine run before CTL-6 still reads `engine:<name>` |
| Card | durable route, a sink family (mcp or toolset), the flow rule not set to deny, the spawn not refused (Child), and something holds the call: an engine's write rule, `writes: ask`, or the flow rule `ask` (every sink script reads first, so the run is tainted) |
| Outcome (sink rows) | legacy with the flow guard on: `refused:approval_unavailable`; legacy with it off: written; durable with `org_deny`: `denied:flow_external_write`; no card: written; card: allow → written (plus a conversation grant), deny → denied, expire → `approval_expired` |
| Effects | 1 when written, else 0, counted by the fake SaaS or MCP server for the turn under test (a regenerate is its own turn) |
| Child | whose model a child run calls (MA1 §3): `inherit` with no role model (no `child.spawned` names a model); `role:same` or `role:different` when the role sets one (the turn's other answers are unchanged); `refused:models.roles` for a forbidden one on the durable route: the spawn is refused `CHILD_GRANT_TOO_LARGE`, the script's tool calls are the sub-agent's so none runs (no card, 0 effects, a sink row's outcome is `denied:child_grant_too_large`), and the parent completes; `unspecified` on the legacy route |

### 4.4 Assertions per row

Every row asserts the oracle's route, executor, card, outcome, effects and child, plus:

| When | Also assert |
|---|---|
| always | journal history checks; first frame `conversation_created`; `RUN_FINISHED` carries a result; no canary string in frames |
| route durable | `run.spec_pinned` before any model call; the pinned spec's tool set equals the agent grant ∩ actor visibility |
| route legacy | no `run.admitted` in the journal for the turn; one `admission.legacy` record carries the reason, the install-wide switch included (QA6-F11) |
| card | the card's reason, journaled as `interaction.explained` in the commit of its `interaction.requested`, names the rule (`flow_external_write`, `engine_writes`, or a layer's own `writes`, `effect_class`, `tool`) and the layer that set it (QA6-F6); the decision is chained in the audit |
| action regenerate | the regenerated turn is a durable run with `regenerateOf` when the route is durable; held calls are asked again |
| route durable | over every connection of the turn, frame ids are unique and in order and each card is announced once (QA6-F12) |
| action reconnect | frames after reconnect start after the last seen id; no duplicate text |
| compiler on | the journaled request digest equals a recompile from the journal (`compile(fold(journal))`) |
| tool_family run_code | an execution receipt; the broker ledger is empty after the run; egress refusals per the sandbox level |
| tool_family memory | the memory band is in the request; a proposal is stored and visible in My memories |
| trigger schedule_* | one run per slot or delivery; the run's actor is the schedule's creator |
| child_model same, different | `child.spawned.data.model = {provider, model, role: default}` on the parent's stream; the child's pinned spec routes to that model alone (`spec.model.policy`) and names its configured entry (`x.pipeshub.model`); the scripted model served a sub-agent call on the role's wire and model, and the coordinator's calls on the turn's wire |
| child_model none (every durable row) | no `child.spawned` of the turn's run has a `model` member |
| child_model forbidden | no `child.spawned` of the turn's run; `tool.denied{rule: child_grant_too_large}`; Node's `child.refused` record names `models.roles`; the memory and run_code assertions do not apply (the tool calls were the sub-agent's) |
| engine set | `workspace.committed` with the file; `engine.checkpointed`; the egress check shows only gateway destinations |
| role admin | `effectivePolicy` and `heldRequests` on the run; the unknown-effects console (when an effect is unknown); the audit chain verifies |
| role operator | `pipeshub_agent_chat_turns_total{path}` moved for the route; no canary in logs or spans; health endpoints green |
| graph, kv, broker | the knowledge result came from the stack's graph; Labs and approval settings round-trip through the stack's KV; dispatch went through the stack's broker |

### 4.5 Runner

- One pytest module per array (`test_pairwise.py`, `test_threeway_core.py`) parametrized by row id, grouped by
  stack with a session fixture per stack (`deployment/e2e/run.sh up` for Compose, `kind_smoke.sh` plus seed for
  Helm), so `pytest -k ST-3` runs one stack's rows.
- API-level by default (SSE client, admin routes, fake SaaS assertions); a row opens a browser only when its
  must-run twin needs one (cards, reload, Stop, a11y). The browser specs live in a `harness` Playwright project
  with `retries: 0` (QA-5).
- Rows with `engine=claude_code` run like every other row; no row is skipped for its engine. Until this runner
  exists, Claude Code's 17 rows and EM-18 run on the engine matrix lane with the same `oracle()`
  (`backend/python/tests/unit/harness/engines/test_qa31_claude_code_rows.py`, Docker lane, QA-31). There, the
  stack, action, trigger, role and sandbox factors are left to this runner.

## 5. Lanes and schedule

| Lane | What runs | Stack | Time budget | Gate |
|---|---|---|---|---|
| Es-PR | EM-01, EM-02, EM-03, EM-13, EM-32 | ST-1 | ≤ 20 min | blocks merge of PRs touching the harness paths |
| Es-nightly | the 44 Es-nightly must-runs; pairwise rows of two stacks per night (ST-1 every night, the others in rotation, all seven within a week); 3-wise rows of one stack per night | rotating | ≤ 120 min | red nightly blocks the next release candidate |
| Es-weekly | every pairwise and 3-wise row on every stack | all | ≤ 6 h | release candidate |
| El-release | EM-18, EM-39, EM-44; a live subset of EM-01, EM-16, EM-17 (k = 3, 2 of 3) | ST-1 | budget, see 06 §7 | release |
| K8s-kind | EM-34; the Helm rows' smoke | kind | ≤ 45 min | PRs touching `deployment/helm/**`; weekly |
| Iso-gVisor, Iso-Kata | EM-42, EM-43; C-28 and the Docker lane's isolation cases under `runsc` and Kata | dedicated runner | ≤ 60 min | release, once the runner exists (QA-25); **needs a dedicated runner** |

### 5.4 Isolation runtimes

Isolation class is not a factor in the arrays: on runc hosts every non-runc row would be blocked, and the
property under test (confinement) does not interact with the chat factors. The isolation lane reruns, under each
runtime: C-13 and C-28, the Docker lane's broker contract, EM-19, EM-20, EM-16 and EM-42/43, plus the SEC3 design
findings (Landlock ABI ≥ 3, kernel ≥ 6.2, the 16-layer limit, Landlock under gVisor). K8s agent-sandbox adds the
provider contract and network-policy probes on a real cluster; kind proves only the install.

## 6. What exists today

| Asset | What it proves | Where it runs | Gap it leaves |
|---|---|---|---|
| `deployment/e2e/run.sh` (+ `durable`) with `frontend/tests/e2e/chat/chat-*-durable*.spec.ts`, `chat-approval-card.spec.ts`, `chat-durable-run-ui.spec.ts`, `workspace/ai-accounts.spec.ts` | Browser → Node → worker → broker → opencode → egress → gateways; cards, reload, Stop, worker kill (DE-5) | **developer machines only**; no workflow calls it | none of it gates a PR (QA-5) |
| `integration-tests/harness/fault/` (`test_crash_points.py` 20 scenarios, `test_upgrades.py`, `test_surface_leaks.py`) | Crash points, Node restart, drain, steering, Slack and MCP surfaces, rolling deploy, rollback | `harness-fault.yml` nightly and on PRs, Redis and Kafka | API-level only; no Redis-down, broker-down or Mongo step-down (IX-G4) |
| `backend/python/tests/unit/harness/engines/test_e2e3_tool_matrix.py` and `test_e2e4_live_matrix.py` | Engine × tool family on real components; live k = 3 | Docker lane; live by hand | no browser |
| `backend/python/tests/cohesion/test_c24_durable_wire_parity.py` | Model requests and outputs equal on both loops for 148 cases | unit workflow (since FX) | offline, not through a deployment |
| `e2e-smoke.yml` (`@smoke`) and `integration-tests.yml` (full Playwright, nightly) | The product's own browser journeys | PR (smoke) and nightly | harness specs are skipped there (they need the `run.sh` env) |
| `integration-tests/mcp_package/`, `integration-tests/ai_agents/` | Published MCP client; live agents | nightly, live | not parametrized by harness path |
| `deployment/helm/tests/kind_smoke.sh` | Chart installs | `helm-chart.yml` | no chat turn |

## 7. Gaps

| Gap | What is missing | Impact | Likelihood | Score | Lane | Depends on |
|---|---|---|---|---|---|---|
| E2E-G1 | No browser e2e of durable chat in CI: every `chat-*durable*`, approval-card and engine spec runs only from `deployment/e2e/run.sh` | 4 | 4 | 16 | Es-PR | none |
| E2E-G2 | No runner that applies a covering-array row and checks the oracle; 0 of 165 array rows automated | 4 | 4 | 16 | Es-nightly | E2E-G1 |
| E2E-G3 | Flow guard across paths (loop, engine, legacy, regenerate, child, schedule) is unit-tested per path but never in one deployment (EM-02..EM-09, EM-15, EM-27) | 5 | 3 | 15 | Es-nightly | E2E-G2 |
| E2E-G4 | Alternative stacks (ArangoDB, etcd, Kafka, Helm) never carry a durable chat turn end to end (EM-34, EM-35) | 3 | 3 | 9 | Es-nightly, K8s-kind | E2E-G2 |
| E2E-G5 | Control-plane journeys (EM-40, EM-41) have no runner yet | 5 | 3 | 15 | Es-nightly | CPD-6, CPD-11 |
| E2E-G6 | Isolation runtimes (EM-42, EM-43) need dedicated runners | 5 | 2 | 10 | Iso-gVisor, Iso-Kata | QA-25 |
| E2E-G7 | No a11y or i18n pass over harness screens (EM-45) | 2 | 4 | 8 | Es-nightly | none |
| E2E-G8 | Cross-org replay over every harness route (EM-32) not automated; schemathesis fuzz from TEST-STRATEGY §3.5 not built | 5 | 2 | 10 | Es-PR | none |
| E2E-G9 | Claude Code rows (10 pairwise, 6 3-wise, EM-18) blocked on the pin decision | 3 | 3 | 9 | El-release | Claude Code pin decision |
