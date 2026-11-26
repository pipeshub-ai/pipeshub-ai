# Agent Harness Runtime — Gaps and Additions (review round 2)

This is a second-pass review of [README.md](./README.md), [one-way-doors.md](./one-way-doors.md), [test-scenarios.md](./test-scenarios.md) and [checklist.md](./checklist.md). It lists what the first pass missed or only touched on. Every gap below has a priority and an acceptance criterion. New one-way doors (OWD-14..19) were added to `one-way-doors.md`. The checklist items are in [checklist.md §20](./checklist.md#20-additions-from-gap-review). The most important new tests were added to `test-scenarios.md`.

Priority: **P0** blocks MVP · **P1** blocks v1 / GA · **P2** v2.

---

## 1. Lifecycle of the things being run

| ID | Gap | Why it matters | Proposal | Acceptance | Pri |
|---|---|---|---|---|---|
| G-01 | **Agent versioning.** Agents (`agentInstances`) are edited in place today, and there are no versions. | A schedule created on Monday can't silently run a different prompt, toolset or harness on Tuesday. Audit has to say *which* agent definition acted. | Make agent definitions immutable and versioned (draft → published), with rollback. Sessions and triggers pin a version, or "latest published" when set explicitly. | A session records its `agent_version`. Editing a published agent creates a new draft. A trigger keeps its pin until the owner promotes. (OWD-14) | P1 |
| G-02 | **Staged rollout of agent and harness changes.** | A bad prompt or harness bump reaches every user at once. | Canary a new agent or harness version to N% of sessions or to named users, compare success, cost and approval rates, then promote or roll back. | Rollout controls in admin and builder, and per-version metrics. | P2 |
| G-03 | **Shadow / dry-run mode.** | Builders need to try a new agent on real data before it can act. | Run with every `write_*` tool stubbed: arguments are recorded and the effects simulated. The UI shows "would have sent email to …". | No external side effects are possible in shadow mode, enforced in the gateway. | P1 |
| G-04 | **Plan-approval gate.** | Before long or background runs, users want to approve *what* the agent will do, not each call. | Optional policy: the harness first produces a plan (Claude plan mode, a `ph plan submit` convention, or a first turn under a read-only policy), and execution starts after approval. Pairs with plan-then-execute injection defence. | Execution tools are denied until the plan is approved. The plan is stored with the session. | P1 |
| G-05 | **Outcome verification gate.** | Agents declare victory early (Anthropic long-running harness post). | A per-agent verifier runs after the harness says it is done: tests pass, citations resolve to text that really exists in the record, the output file opens, the schema validates. On failure the session either continues or is flagged. Reuse the idea behind the `completion_gate` hook. | The session state shows `verified` / `unverified` with reasons. | P1 |
| G-06 | **Undo / rewind.** | "Revert what the agent just did" is a top user request. | Workspace: checkpoint each turn and rewind to any of them (like Claude Code checkpoints). External writes: record a compensating action where the toolset supports one (delete the created ticket, recall a draft) and offer it in the UI. | Rewinding the workspace restores files and git state. Compensation is offered and audited where it is available. | P2 |
| G-07 | **End-of-session summary and "what changed" report.** | Background runs need a digest a human can skim. | An auto-generated summary listing actions taken, records touched, artifacts, cost, approvals and open questions. It is attached to notifications. | Present for every terminated session. Sent with completion notifications. | P1 |
| G-08 | **Session export and portability.** | Users and auditors want the transcript and workspace offline. Developers want to continue locally (Claude Code `--teleport`-style). | Export a redacted transcript (JSON/Markdown) plus the workspace as an archive or git bundle. Optionally hand off to a local CLI with a short-lived pull token. | Export respects ACL and redaction. The local hand-off never exports secrets. | P2 |

## 2. Interaction and collaboration

| ID | Gap | Why it matters | Proposal | Acceptance | Pri |
|---|---|---|---|---|---|
| G-09 | **Session sharing and multiple participants.** | Teams want to watch, comment on, co-steer or take over a session. | Roles per session: viewer, commenter, steerer, approver. The **principal never changes**: a steerer from another team does not gain the principal's data access through the agent, and their messages are labelled. Ownership transfer means starting a new session under the new principal. | ACL tests: a viewer can't approve, and a steerer's messages are attributed to them. (OWD-19) | P1 |
| G-10 | **Human takeover of the workspace.** | Developers want to drop into the shell, fix something, and hand back. | An interactive terminal with write access (opt-in, audited), then a "hand back" that tells the harness what changed. | The keystroke session is recorded. The harness gets a diff summary on resume. | P2 |
| G-11 | **Authenticated preview URLs / port forwarding.** | Web-app tasks need to see the running app. Browser verification needs it too. | Per-session preview URLs through the Node gateway: the session ACL is enforced, the URL is never public, and it expires with the session. | An unauthenticated request is rejected. The URL dies at terminate. | P2 |
| G-12 | **Auth-required pause and auto-resume.** | Today a missing toolset credential returns an error (WS-09). The better UX is "connect Jira → continue". | The Tool Gateway returns `auth_required` and the session pauses with a connect card. After OAuth completes, the pending call is retried automatically. | The pending call runs once after connect, with no manual re-prompt. | P1 |
| G-13 | **Unified approvals and questions inbox.** | Approvers juggle many sessions. | A cross-session inbox with filters and batch approve for identical low-risk patterns. Mobile-friendly. | Batch approve applies only to identical tool + argument patterns. | P1 |
| G-14 | **Microsoft Teams, email-to-agent, and calendar surfaces.** | The plan has Slack only. Many enterprise customers live in Teams. | Treat channel adapters as triggers plus reply sinks, behind one interface. | A Teams mention starts a session, and approvals work in Teams. | P2 |
| G-15 | **User feedback capture.** | There is no signal for what failed from the user's point of view. | Thumbs up or down plus a reason on sessions and answers. A negative rating with consent turns into a golden-trace candidate. | Feedback is linked to session and agent version. | P1 |
| G-16 | **Speculative pre-warm.** | Latency. | Start claiming and materializing a sandbox when the user opens an agent or starts typing, and release it if no message arrives within N seconds. | Session-ready time measured from send drops. Pre-warm cost is capped per user. | P2 |

## 3. Environment and workspace capabilities

| ID | Gap | Why it matters | Proposal | Acceptance | Pri |
|---|---|---|---|---|---|
| G-17 | **Environment definitions.** | Real repos need toolchains, setup scripts, non-secret env vars and caches. `devcontainer.json` is the de facto standard. | An agent-level or repo-level **Environment**: base image, setup script, a `devcontainer.json` subset, env vars, and caching rules. Snapshot it after setup to make the next start fast. | Second-session start uses the post-setup snapshot. | P1 |
| G-18 | **Service sidecars** (Postgres, Redis, Elasticsearch for tests). | Coding tasks need test services, and "no secrets in the sandbox" must still let tests connect. | Sidecars like GitHub Actions service containers: on a private network shared only with that sandbox, using generated throwaway credentials (not real secrets), and destroyed with the session. | Sidecars can't be reached from other sessions. The credentials are ephemeral. | P2 |
| G-19 | **Dependency, build and repo caches.** | `npm ci` or `pip install` on every session wastes minutes and egress. | Per-org read-only cache volumes (package caches, git mirrors) filled by a trusted job, never written by sessions. Sessions write to a copy-on-write layer. | No cache poisoning across sessions (see SEC-SC-06). p95 setup time drops measurably. | P1 |
| G-20 | **Custom images and corporate CA trust.** | Enterprises have internal toolchains, internal CAs and TLS-intercepting corporate proxies. | Customer images built `FROM` a PipesHub base, scanned and signed, then registered in the catalog. Corporate CA bundles are injected into the trust store. | Unsigned custom images are refused. TLS to internal hosts works without disabling verification. | P1 |
| G-21 | **Upstream proxy chaining and private-network destinations.** | A deployment's egress must go through *its own* proxy. Agents need internal Git, Artifactory and internal APIs. | The egress proxy supports an upstream proxy. Private CIDRs are denied by default, with explicit allow entries per host:port. **The PipesHub internal data stores are always denied** (G-38). | See SEC-EG-13 and SEC-ISO-09. | P1 |
| G-22 | **Persistent project workspaces.** | PipesHub already has projects (`project:read/write` scopes). Users expect a workspace that persists across sessions. | A workspace bound to (project, principal), or to a project-shared service-account principal. GC policy, quotas, and a visible size. | Covered by OWD-16. A shared workspace never mixes principals' private data without a visible label. | P2 |
| G-23 | **Safe web-research tier.** | Default-deny egress breaks knowledge-worker journeys that need the public web, yet open egress plus private data is the lethal trifecta. | Web access goes only through a PipesHub fetch/search tool. That tool is GET-only, strips cookies and auth, caps response size, runs from the control plane rather than the sandbox, and sets the `untrusted_content_read` taint. The harness's own WebFetch/WebSearch is disabled or redirected to it (G-24). | Web research works with sandbox egress still denied. Query strings carrying private data trigger DLP (SEC-EG-08). | P1 |
| G-24 | **Governance of the harness's built-in tools.** | Harnesses ship their own WebSearch, WebFetch, Task, Bash and notebook tools. Some depend on provider-side features: Claude's WebSearch is an Anthropic server tool and fails through a DeepSeek or OpenAI backend. | Each harness profile has a built-in-tool policy: allow, deny, or replace with a PipesHub equivalent. It is enforced through native config (Claude `--disallowedTools`, Codex config, OpenCode `permission`) and checked in conformance. | No built-in tool silently bypasses egress or policy. Provider-dependent tools are disabled when the backend can't serve them. | P0 |
| G-25 | **Injecting PipesHub policy through harness hooks.** | The plan bridges permission prompts, but harness hooks (Claude PreToolUse, Codex managed hooks, Gemini BeforeTool) can enforce policy on *every* native tool call, not only on prompted ones. | Ship a read-only, PipesHub-managed hook that calls the policy engine through `ph policy check` for Bash, Write and WebFetch. Defence in depth only; the outer sandbox stays the boundary. | The hook can't be edited by the agent. A deny reaches the harness with a reason. | P1 |
| G-26 | **Harness extension assets** (custom commands, subagent definitions, plugins, output styles). | Builders want to ship `.claude/commands`, `.claude/agents`, Codex prompts and OpenCode agents with an agent. | Treat these as versioned agent assets (like skills): signed, materialized per harness, and kept out of the repo's own config. | Assets are reviewed like skills. A repo can't add its own without allowlisting. | P2 |
| G-27 | **Secondary-model routing.** | Harnesses use more than one model: Claude Code uses a small fast model for background tasks, and subagents or compaction may use others. | Harness profiles define role → model mappings (primary, fast/background, subagent, compaction). The gateway enforces that every role maps to an allowed model. | No request reaches an unmapped model. Costs are reported per role. | P1 |
| G-28 | **Headless browser inside the sandbox.** | UI verification and web-app tasks need Playwright. `browser_sandbox.py` already exists. | An optional browser in the image or a sidecar. Egress policy applies to it, and it gets no access to the user's browser cookies. | The browser respects the egress allowlist. Screenshots can be emitted as events. | P2 |

## 4. Data, knowledge and PipesHub-specific

| ID | Gap | Why it matters | Proposal | Acceptance | Pri |
|---|---|---|---|---|---|
| G-29 | **Policy inputs beyond taint.** | Some organizations want policy to depend on more than the boolean taint labels. | An extension point an edition can bind, asked before model calls, egress, retention and sharing; with none bound nothing changes. | With no extension point bound, every decision is the same as without one. (OWD-15) | P1 |
| G-30 | **Model data-handling policy.** | Some organizations restrict which models may receive which data. | An extension point every model route asks before it is used (pairs with G-29). | With none bound no call is refused; a bound one's refusal carries a reason. | P1 |
| G-31 | **Shared connector rate budgets.** | Agent tool calls and connector sync hit the same Slack, Google and Jira quotas. A busy agent can stall indexing, and the reverse. | A per-(org, connector) rate budget shared by the Tool Gateway and the connectors service, with priority: interactive agent > sync > background agent. | A load test with agents plus a sync job shows no starvation and no 429 storms. | P1 |
| G-32 | **Read-after-write.** | The agent creates a Jira ticket or a Drive doc, then searches and doesn't find it, because indexing lags. | Write tools return the created record in a canonical shape. The session keeps a "recent writes" overlay that `knowledge_search` merges until indexing catches up. Indexing is optionally triggered on the write. | Searching right after a write finds the new item, marked "pending index". | P2 |
| G-33 | **Agent outputs become knowledge.** | Briefs and reports agents produce should be findable later, with the right ACL. | Artifacts are optionally indexed as records with the principal's permissions and lineage back to their sources. A derived artifact never gets broader ACLs than its most restrictive source, unless the owner shares it explicitly. | An indexed artifact is visible only to its permitted users. Lineage is visible. | P2 |
| G-34 | **Searching past sessions.** | "What did the agent find last week about X?" | Index session summaries (G-07) and, optionally, transcripts, ACL'd to session viewers. | Search returns only sessions the caller can view. | P2 |
| G-35 | **Bulk data access.** | Analysis tasks need thousands of records. Paging through `knowledge_fetch` is slow and token-heavy. | A bulk export tool that writes JSONL into `/pipeshub/knowledge/` asynchronously (MCP Tasks-style). | A 10k-record export completes, is ACL-filtered, and is audited. | P1 |

## 5. Security additions

| ID | Gap | Why it matters | Proposal | Acceptance | Pri |
|---|---|---|---|---|---|
| G-36 | **Knowledge reads and exports pass an extension point.** | An organization may want its own controls on what an agent reads or exports. | Reads and exports ask an extension point an edition can bind; with none bound they are ACL-checked and audited as before. | SEC-AZ-09. | P1 |
| G-37 | **Secrets the user pastes into chat.** | "Use this staging key: sk-…" sends a secret to the model, the transcript and the logs. | Detect secrets in user input, warn, and offer to store the value in the vault and expose it as a scoped credential through the proxy. Redact it from the transcript. | SEC-CR-07. | P1 |
| G-38 | **Sandbox access to the control plane's own data stores.** | SEC-ISO-04 covers sandbox ↔ sandbox only. A sandbox reaching Mongo, etcd, Redis, Kafka, Neo4j, Qdrant or internal service ports would be catastrophic. | NetworkPolicy and proxy deny every internal service. The only reachable endpoints are the gateways through the proxy. | SEC-ISO-09. | P0 |
| G-39 | **Content safety and acceptable use.** | Org policy on harmful or prohibited content (malware authoring, harassment) applies to agents too. | Input and output guardrails at the Model Gateway, reusing the `GUARDRAIL_INPUT`/`GUARDRAIL_OUTPUT` concepts, with org-configurable categories. | Blocked categories produce a clear refusal event and an audit record. | P2 |
| G-40 | **External policy webhook.** | Security teams want their own DLP or approval systems in the loop. | Optional synchronous external policy webhook for `write_external` decisions, with a timeout that fails closed. Streaming audit events elsewhere is an extension point. | A webhook timeout means deny. | P1 |
| G-41 | **Group-based policy (SSO/SCIM groups).** | Policies are usually written per team, not per user. | Policy subjects can be IdP groups. Group changes propagate within N minutes. | Removing a user from a group revokes the related harness or model access for new sessions. | P1 |
| G-42 | **Identity on external writes and AI disclosure.** | Git commits, emails and tickets created by an agent need a consistent author identity and, depending on org policy, an AI disclosure. Downstream audit depends on it permanently. | Configurable: act as the user ("via PipesHub agent X" footer or header), or as a bot identity with `Co-authored-by` the user. Git commits are signed with a bot key held outside the sandbox. | Every external write carries the configured identity and disclosure. (OWD-18) | P1 |
| G-43 | **Vendor subscription logins** (Claude Max/Pro, ChatGPT plan login inside harnesses). | Users will ask to "use my subscription". That means vendor OAuth tokens in the sandbox, ToS questions, and spend outside our metering. | Decide explicitly (OWD-17). Recommended: not supported in v1; API keys go through the gateway only. Revisit if vendors offer delegated, gateway-compatible auth. | The harness login flow is disabled in images. Conformance verifies it. | P0 (decision) |
| G-44 | **Legal hold and eDiscovery.** | Agent transcripts and actions are business records. | Put a legal hold on sessions, artifacts and audit for a custodian. Holds override retention deletion. | A held session survives its retention TTL. Export is available to authorized legal roles. | P2 |

## 6. Operations, scale and quality

| ID | Gap | Why it matters | Proposal | Acceptance | Pri |
|---|---|---|---|---|---|
| G-45 | **Priority classes and preemption.** | Interactive sessions must not queue behind 500 scheduled jobs or batch runs. | Classes `interactive > approval-resume > background > batch`, applied to sandbox claims, gateway concurrency and provider rate limits. Lower classes can be suspended or preempted. | Under saturation, interactive p95 session-ready stays within SLO. | P1 |
| G-46 | **Warm-pool autoscaling.** | Fixed pools either waste money or run dry. | Size pools by time of day and demand, with min/max per template, scale-to-zero on Compose, and a reported cost of idle pools. | Pool hit rate ≥ target. Idle cost is visible. | P2 |
| G-47 | **Availability SLO, error budget and DR.** | The latency SLOs exist; availability and disaster recovery don't. | Availability SLO for session API and gateways (for example 99.9%). RPO/RTO for the event store and artifacts. A tested multi-zone story for Helm. | Recorded DR drill. SLO dashboards. | P1 |
| G-48 | **Graceful degradation.** | When there is no sandbox capacity, or a harness is broken, users get nothing. | Fallback chain per agent: queue with ETA → alternate harness or provider → native PipesHub agent without code tools (if the agent allows it). The user sees what changed. | A chaos test with the provider down still answers knowledge-only prompts. | P1 |
| G-49 | **Test infrastructure for the runtime itself.** | Deterministic CI needs fakes. | A **mock harness adapter** (a scripted event stream), record/replay of model traffic (VCR) at the gateway, a seeded fixture tenant (Drive/Slack/Jira/Confluence data with ACL variety), and ephemeral test environments per PR. | CI runs the P0 journeys with no live models or connectors. | P0 |
| G-50 | **Local developer mode for PipesHub engineers.** | Contributors on macOS can't run gVisor. | A Compose dev profile using runc plus the dev/insecure flag, mock harness, stub gateways and seeded tenant, documented in CONTRIBUTING. | One command brings up a working harness session locally. | P1 |
| G-51 | **Product analytics.** | Is it working for users? | Track adoption, session success, abandonment, approval rates, time-to-value, cost per successful session, and per-harness/per-model comparisons. Uses the existing telemetry pipeline, privacy-preserving. | Dashboard for product and customer success. | P2 |
| G-52 | **Time, timezone and locale.** | Schedules across DST, "today" in prompts, output language. | Cron is stored with an IANA timezone. The current date and timezone are injected at the **tail** of context (keeps the cache prefix stable). User locale sets output language unless overridden. | DST transition test. Cache ratio is unaffected by the date injection. | P1 |
| G-54 | **Chargeback / showback.** | Departments want cost allocation. | Cost ledger dimensions include team/cost-center (from IdP attributes or project). Reports over them are an extension point. | Per-dimension totals reconcile with the ledger total. | P2 |

## 7. Capability ideas worth considering

| ID | Idea | Value | Pri |
|---|---|---|---|
| G-55 | **Cross-harness best-of-N and review.** Run Claude Code and Codex on the same task in parallel worktrees, then have one review the other or let the user pick. This reuses `best_of_n` with `HarnessAgentRunner`. | Quality on hard tasks; a built-in A/B between harnesses. | P2 |
| G-56 | **Graduated autonomy.** An agent's approval requirements relax as it builds a track record (N approved actions without edits), within admin limits, and tighten again after a deny or incident. | Less approval fatigue without giving up control. | P2 |
| G-57 | **Initializer → incremental pattern as a product feature.** For multi-day goals, PipesHub creates the feature list, progress file and init script, then runs one verified increment per session on a schedule. | Long-horizon reliability (Anthropic long-running harness). | P2 |
| G-58 | **Quarantined summarizer tool** for bulk untrusted content (map-reduce over Slack or email with no tool access). It returns constrained, structured output to the main agent. | Prompt-injection containment on the riskiest data. | P1 |

---

## Where these land

- **New one-way doors:** OWD-14 (agent versioning and pinning), OWD-15 (data classification in the policy model), OWD-16 (workspace ownership and sharing), OWD-17 (vendor subscription logins), OWD-18 (identity and disclosure on external writes), OWD-19 (multi-participant session semantics). See [one-way-doors.md](./one-way-doors.md#additions-from-gap-review).
- **New tests:** SEC-ISO-09, SEC-AZ-09, SEC-CR-07, SEC-EG-13, SEC-SC-06, plus §13 of [test-scenarios.md](./test-scenarios.md#13-additions-from-gap-review).
- **Checklist:** [checklist.md §20](./checklist.md#20-additions-from-gap-review).

---

## Status

Updated by the work packages in the project tracker. ☐ not started · ◐ partial · ✅ passing (test path or commit) · ⏸ deferred (reason). This table starts at ☐ and is ticked only when a work package lands its test.

| ID | Status | Test / commit |
|---|---|---|
| G-01 | ✅ | `backend/python/tests/unit/api/routes/test_ga01_agent_version_routes.py::test_ga01_a_draft_does_not_reach_runs_and_a_schedule_keeps_its_pinned_version`; see C20.02 in the project tracker |
| G-02 | ⏸ | P2, v2 scope |
| G-03 | ✅ | `backend/python/tests/unit/harness_core/gates/test_ga02_dry_run.py`; see C20.04 in the project tracker |
| G-04 | ✅ | `backend/python/tests/unit/harness_core/gates/test_ga03_plan_approval.py`; see C20.05 in the project tracker |
| G-05 | ✅ | `backend/python/tests/unit/harness_core/gates/test_ga04_verification.py`; see C20.06 in the project tracker |
| G-06 | ⏸ | P2, v2 scope |
| G-07 | ✅ | `backend/nodejs/apps/tests/modules/agent-harness/projection/ga05-run-digest.test.ts` |
| G-08 | ⏸ | P2, v2 scope |
| G-09 | ✅ | `backend/nodejs/apps/tests/modules/agent-harness/support/collaborator-turns-contract.ts` "CA-2: a collaborator's turn runs as the collaborator" |
| G-10 | ⏸ | P2, v2 scope |
| G-11 | ⏸ | P2, v2 scope |
| G-12 | ✅ | `backend/python/tests/unit/harness_core/durable/test_ga07_connect_pause.py`, `frontend/app/(main)/chat/components/message-area/__tests__/ga07-connect-card.test.tsx`; see C20.13 in the project tracker |
| G-13 | ✅ | `backend/nodejs/apps/tests/modules/agent-harness/approvals-queue.test.ts`, `backend/nodejs/apps/tests/modules/agent-harness/question-inbox.test.ts`, `backend/nodejs/apps/tests/modules/agent-harness/wp54-answer-continues-model.test.ts` (the Inbox's approvals and questions; a batch decide across conversations is an extension point); see C20.14 in the project tracker |
| G-14 | ⏸ | P2, v2 scope |
| G-15 | ✅ | `backend/nodejs/apps/tests/modules/agent-harness/wp36-feedback.test.ts` (feedback capture and consent) |
| G-16 | ⏸ | P2, v2 scope |
| G-17 | ✅ | see C20.18 in the project tracker (WP-100: pinned into the run spec and the builder section; WP-132: the post-setup snapshot is the `EnvironmentSnapshotter` extension point; `backend/python/tests/unit/harness_core/engines/test_wp100_environment_pin.py`, `backend/python/tests/unit/harness_core/engines/test_wp91_environments.py`) |
| G-18 | ⏸ | P2, v2 scope |
| G-19 | ✅ | see C20.20 in the project tracker (WP-132: org caches are the `SharedCacheMounts` extension point, none in this build; `backend/python/tests/unit/harness_core/engines/test_cache_mounts.py`) |
| G-20 | ✅ | The broker's image check is an extension point (`ImageTrustProvider`; default: the release's engine images, verified against the release roots when signatures are required): `backend/python/tests/unit/harness/supply_chain_trust/test_wp129_supply_chain_ports.py` (SCP-1..SCP-6), `backend/python/tests/unit/harness/supply_chain_trust/test_wp85_image_trust.py` (IT-1..IT-4, IT-10, IT-11). Custom image catalogs and a corporate CA plug in there; none in this build. See C20.21 in the project tracker |
| G-21 | ✅ | `backend/python/tests/unit/harness_core/sandbox/egress/test_g21_upstream_egress.py`, docker `backend/python/tests/unit/harness/sandbox_broker/test_egress_upstream_docker.py`; see C20.22 in the project tracker |
| G-22 | ⏸ | P2, v2 scope |
| G-23 | ✅ | `backend/python/tests/unit/web/test_ga12_web_research_tier.py` (`test_ga12_*`); see C20.24 in the project tracker |
| G-24 | ✅ | `backend/python/tests/unit/harness_engines/test_fg2_no_native_web.py::test_claude_code_runs_without_web_fetch_or_web_search` |
| G-25 | ✅ | see C20.26 in the project tracker; `backend/python/tests/unit/harness_core/engines/test_ga14_native_policy.py`, `backend/python/tests/unit/harness_engines/test_ga14_policy_hooks.py`, `backend/python/tests/unit/harness_engines/pipeshub/test_sk05_ph_skill_and_policy.py::test_ga14_*`; D `backend/python/tests/unit/harness/engines/test_real_claude_code_conformance.py::test_ga14_c20_26_the_managed_hook_still_refuses_after_the_agent_tries_to_disable_it`, `backend/python/tests/unit/harness/engines/test_real_codex_conformance.py::test_ga14_c20_26_the_managed_hook_refuses_a_command_the_runs_policy_refuses` (WP-42) |
| G-26 | ⏸ | P2, v2 scope |
| G-27 | ✅ | see C20.28 in the project tracker; `backend/python/tests/unit/harness_core/gateway/test_ga15_model_roles.py`, `backend/nodejs/apps/tests/modules/agent-harness/ga15-model-roles.test.ts` (GA-15, MROLE-1..11, WP-73) |
| G-28 | ⏸ | P2, v2 scope |
| G-29 | ✅ | An extension point with no default in this build (`ClassificationPort`, `HarnessEdition.classification`, `RetentionPass`); the parser stays shared: `backend/python/tests/unit/harness/policy/test_classification_ports.py`, `backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts` |
| G-30 | ✅ | `ModelPlacementPort`, an extension point with no default in this build (default `ALLOW_EVERY_MODEL`): `backend/python/tests/unit/harness/policy/test_placement_ports.py` |
| G-31 | ✅ | `backend/python/tests/unit/services/rate_budget/test_ga17_shared_budget_load.py`, `backend/python/tests/unit/services/rate_budget/test_wp37_rate_budget.py`, `backend/python/tests/unit/harness/test_wp37_rate_budget_wiring.py` (WP-37, Slack and Notion); `backend/python/tests/unit/services/rate_budget/test_ga17_vendor_families_load.py`, `backend/python/tests/unit/harness/test_wp38_rate_budget_families_wiring.py` (WP-38, Jira, Confluence, Google and Microsoft) |
| G-32 | ⏸ | P2, v2 scope |
| G-33 | ⏸ | P2, v2 scope |
| G-34 | ⏸ | P2, v2 scope |
| G-35 | ✅ | WP-40 (= C20.36, GA-19): `backend/python/tests/unit/harness_core/knowledge/test_ga19_bulk_export.py`, `backend/python/tests/unit/harness_core/gateway/test_ga19_export_through_the_gateway.py`, `backend/python/tests/unit/harness/test_wp40_knowledge_export_wiring.py` |
| G-36 | ✅ | = C20.37: `backend/python/tests/unit/harness/test_wp40_knowledge_export_wiring.py` (BW-4), `backend/nodejs/apps/tests/modules/agent-harness/edition-journal-seams.test.ts` |
| G-37 | ☐ | |
| G-38 | ◐ | see C20.39 in the project tracker (runc ✅ WP-14; gVisor, Helm NetworkPolicy ⏸) |
| G-39 | ⏸ | P2, v2 scope |
| G-40 | ⏸ | not in this build: the external policy webhook; the worker's `ExternalPolicyGate` leaves the run's rules unchanged |
| G-41 | ✅ | see C20.42 in the project tracker; no group access is bound in this build (`backend/nodejs/apps/tests/modules/agent-harness/org-policy-defaults.test.ts`); the harness's checkpoints for a bound one: `backend/nodejs/apps/tests/modules/agent-harness/sec-c02-model-access-one-path.test.ts` (GA-21, GRP-1..27, WP-73) |
| G-42 | ◐ | = C20.43: WP-23 identity and disclosure, WP-90 wording per action type; WP-132: the identity per action type is the `WriteIdentityPort` extension point; bot-key commit signing waits on OB-1 |
| G-43 | ✅ | GA-23: `backend/python/tests/unit/harness/security/test_ga23_vendor_logins_refused.py` (WP-06) |
| G-44 | ⏸ | P2, v2 scope |
| G-45 | ✅ | WP-44, WP-48, WP-49: see C20.46 in the project tracker; GA-24 `backend/python/tests/unit/harness/test_g45_preemption.py` (with the mixed loop and engine load) |
| G-46 | ⏸ | P2, v2 scope |
| G-47 | ◐ | targets, error budget, RPO/RTO in `docs/agent-harness-v2/SLO.md` and the drill procedure `docs/agent-harness-v2/DR-DRILL.md` (WP-46, `backend/python/tests/cohesion/test_v2_guides.py`); left: a recorded drill and availability dashboards |
| G-48 | ✅ | see C20.49 in the project tracker (WP-89: the chain, its notices and GA-25; WP-100: the alternate model on by default, engines through the Model Gateway; `backend/python/tests/unit/harness/test_wp100_ga25_default.py`, `backend/python/tests/unit/harness_core/gateway/test_wp100_gateway_fallback.py`) |
| G-49 | ◐ | mock harness, VCR gateway, seeded tenant and the GA-26 lane pass: `backend/python/tests/harness_replay/test_ga26_p0_journeys.py`; left: per-PR ephemeral environments (owner) |
| G-50 | ◐ | see C20.51 in the project tracker |
| G-51 | ⏸ | P2, v2 scope |
| G-52 | ✅ | see C20.53 in the project tracker (WP-89: the loop and the cache-ratio replay; WP-100: engine, child and scheduled runs; `backend/python/tests/unit/harness_core/engines/durable/test_wp100_engine_answer_language.py`, `backend/nodejs/apps/tests/modules/agent-harness/wp100-output-language.test.ts`) |
| G-54 | ⏸ | P2, v2 scope |
| G-55 | ⏸ | P2, v2 scope |
| G-56 | ⏸ | P2, v2 scope |
| G-57 | ⏸ | P2, v2 scope |
| G-58 | ☐ | |
