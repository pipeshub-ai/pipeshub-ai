# ADR-AGB-01: Agents built by asking, the owner's "ask me first", tool effect classes, skills and MCP address checks

- Builds: the UI revamp's P4 backend gaps UIP4-FU-1..5 ("UI-P4 evidence" in the project tracker), under the plan's decisions D1–D9, decided from first principles, long term, UX-first. UI-B4.
- Related: ADR-WF-01 D7 (build by asking: `propose_workflow`, the pattern this follows), C4.08 (portable agents and their import), ADR-OWD-09 (policy and approvals; layers only narrow), main #4313 (deletes and sends ask), ADR-UI-01 (a person's run lists), ADR-SEC-02 (org binding), docs/agent-harness-v2/impl/tool-effects.md (declared effects).

## Context

Phase P4 built Agents, Skills and Connectors in the new interface and found what the backend lacked: the agent builder read the person's sentence with fixed English rules; an agent's owner could not store "check with me first" for an in-chat agent (only an admin, through the whole-org approvals document); the interface guessed a tool's read or write class from its name and read up to 20 agent details to draw one page of the index; a skill proposal could not be approved with edits in one version, only admins decided proposals, saves had no note, skills had no rename, categories were bare strings, and nothing said which agent learned a proposal; and nothing checked an MCP server's address before one was created.

## Decision

**D1. An agent is drafted by the model and created only on the person's confirmation.** The chat agent's internal tool `propose_agent` creates nothing: it validates a draft against `ext/pipeshub/api/agent-proposal.schema.json` (name, description, instructions, start message, knowledge by name and connector type, toolsets by name with the chosen tools and optionally the instance's name, skills, MCP servers by name with their tools or all, `mode` answers or acts, `askBeforeActing`) and answers a question card with "Create agent / Change something / Not now" and the draft as `agent` (journaled `x.pipeshub.question.asked`; the run ends `needs_input`, as `ask_user_question` does). The model writes it in the person's language, so it works in every language. An acting agent is drafted safe: `askBeforeActing` defaults to true; an answering one carries none.

- **Confirming is the person's own request**, `POST /api/v1/agent-harness/agent-proposals/{conversationId}/runs/{runId}/{toolCallId}/confirm`: Node reads the draft from the asking run's journal (`RunTimelineService.questionCard`), never from the body, only for the run's own person in its org on a chat they may still open (anyone else: 404), only from a `propose_agent` call, and checks it against the contract again (422 otherwise). `GET` of the same path reads it (`building`, or `created` with the agent).
- **The agent is made through the agents service's import (C4.08) with the person's own credential**: the draft maps to a portable agent (names only), so it is theirs, in their org, shared with no one, and every name binds only to what they may use; what does not bind is left out and returned as `unresolved` (kind, name, reason). The import gains `bind: sole_instance`: an action named without an instance takes the person's one instance of its type, and is listed as ambiguous when they have several. The mode is not a filter: an answering agent may still read through an action.
- **Once per proposal**: the `agent-proposals` store (in-memory, Mongo `agentProposals`, PostgreSQL `harness.agent_proposals`, `harness_0013`) claims the proposal before creating (`creating`), names the agent after (`created`); a second confirmation answers the same agent, one sent meanwhile is told to try again (409), a create that failed releases the claim, and a claim stalled for five minutes (a restart) may be taken over. Ids and names only. User and org deletion erase the person's records.
- **"Check with you before it acts"** is stored after creation as the agent's own rule (D2) through the `AskFirstRule` port; the view says `stored`, `not_stored` (the workspace's rules decide) or `not_asked`.

**D2. An agent's owner and editors set its own approval rule, only stricter.** `GET`/`PUT /api/v1/agent-harness/agents/{agentKey}/approvals` (`{rule: ApprovalRules | null}`; "ask me first" is `{writes: 'ask'}`). The agents service decides who may, with the caller's own credential (`can_edit`): the owner, editors and admins write, readers read (`canEdit: false`), anyone else is told it does not exist.

- **One rule slot per agent**, the one the admin's approvals page writes (`orgAgents[org][agent]` in the KV document, the agent layer locked by the org in the policy store), saved through the same `ConfigApprovalSettings.write`, so every `CONTROL_PLANE_POLICY_STORE` mode stays one path and the worker reads it as it always did (layers only narrow).
- **Stricter only**: a save must be at least as strict (`approvalsWithin`) as its floor, the strictest of the deployment's default, the org's rule and any rule an admin set for that agent; a looser one is refused `APPROVAL_RULE_LOOSER`. `null` takes the owner's rule away and gives back the admin's; on a rule an admin set it counts as looser.
- **Who set it** is kept in the `agent-approval-rules` store (`{ownerRule, adminRule, updatedBy, updatedAt}`, in-memory, Mongo, PostgreSQL `harness_0013`). A record whose `ownerRule` no longer equals the slot reads as none: the slot is the admin's, the stricter reading. The slot is written first and the record second, so a crash in between fails closed. An admin's save drops the records of the agents whose rule it changed.
- **Audited** as `policy-section` `agent:<key>/approvals` with the person as actor, in the same audited transaction as an admin's save.
- An engine agent keeps `engineProfile.approvals.write` for what its engine does inside the sandbox; this rule decides every tool call of every agent.

**D3. Tools say what they do; the agents list says how each is used.**

- Every toolset and MCP tool listing carries `effect` (`read` | `write`) and `effectClass` (the declared class or null), from the tool's declared effect: a toolset tool's `effect` tag (what its catalog descriptor and a run's pin carry), an MCP tool's annotations as a run reads them (`read` only with `readOnlyHint: true`). Names never decide. Only a declared `read` is `read`; `workspace`, `execute`, none or an unknown value list as `write` (fail closed). One mapping: `app/agents/registry/tool_effect.py`.
- `GET /api/v1/agents` rows carry `usage` (`ext/pipeshub/api/agent-usage.schema.json`): `toolsets`, `knowledge`, `mcpServers` (counted from the list query, no extra read), `chatsUsedIn` and `lastUsedAt`, the caller's own, from the run lists (ADR-UI-01) in one aggregate per page (`ExecutionStore.agentUsage`, at most 200 agents, on the existing `runs_by_actor_agent` index). With the harness off or its store failing, those two are null (unknown), never zero.

**D4. Skills.** A proposal (candidate) is approved with the reviewer's edits as one version (`POST /skills/candidates/{id}/approve {name?, description?, body?, category?, subcategory?, tags?, changeNote?}`). The person whose run learned it (`proposedBy`) decides it, as an admin may; anyone else is refused, and members list only their own candidates. Each candidate records the agent (`source_agent_key`, null for the assistant) and the run that learned it. Saves take a `changeNote`, kept on the version and shown in history. A rename changes the name people see (`displayName`, `POST /skills/{name}/rename`), never the id every reference keys on (agent links, installations, pins, `/command`); a name another skill of the org already shows is refused.

**Skill categories** are an org registry (`agent-harness/skill-categories`, in-memory, Mongo, PostgreSQL `harness_0013`; `GET`/`POST /api/v1/agent-harness/skill-categories`, `PATCH`/`DELETE …/{key}`): a `key` (the string skills already carry, never changed), a `label` and a `colour` token (`grass`, `sky`, `amber`, `plum`, `cyan`, `orange`, `indigo`, `pink`, `teal`, `brown`; null: the app's default). A rename changes the label, so no skill is rewritten; an empty category may be registered; the list merges the registry with the categories skills carry (read from the skills service with the caller's credential; when it cannot answer, the registry alone, `inUseKnown: false`). Anyone who may write skills adds an empty category; registering a name skills already carry is an admin's; rename, recolour and delete are an admin's or the category's creator's; delete removes the entry only.

**D5. An MCP server's address is checked before anything is created.** `POST /api/v1/mcp-servers/probe {url, transport?}` (`mcp:write`, 20 a minute per person) answers whether a server answers there and which sign-in it needs (`ext/pipeshub/api/mcp-probe.schema.json`): `none` (an `initialize` without credentials answered), `oauth_dcr` (OAuth with a registration endpoint or the client metadata document), `oauth_app` (OAuth that needs a registered app; `redirectUri` given), `token` (refused without credentials and no OAuth metadata), the transport (`streamable_http` or `sse`), the server's name; or `reachable: false` with a stable reason (`invalid_url`, `blocked_address`, `unreachable`, `timeout`, `not_mcp`, `tls_error`, `redirect_blocked`). Nothing is stored. Every fetch (initialize, the metadata, URLs the metadata names) goes through the URL guard (`url_guard.PerOriginGuardedTransport`): each origin checked and its connection pinned to the checked address; loopback, link-local and metadata addresses always refused; metadata hosts public only unless they are the typed host; redirects not followed except on the same origin (at most 2, each re-checked); 5 s a request, 25 s in all, 64 KiB read, no credentials or cookies sent. A member gets the public-only policy of a personal server, an admin the org's.


## As built

See the project tracker "UI-B4 evidence". UI-ADOPT2 (2026-11-09) closed two of UI-B4's deviations:

- **An approved proposal is its learner's.** Whoever approves it (the learner or an admin), the skill is created as the person whose run learned it (`createdBy` and the OWNER edge are theirs, through a skills manager acting as them with their own visibility); the approver is recorded apart, `approvedBy` (user id) and `approvedAtTimestamp` on the skill, shown as `approvedBy` on the skill list and `GET /skills/{name}`. A proposal with no recorded learner (one queued before UI-B4) stays the approver's.
- **A draft's tools are checked at confirmation.** On the chat-drafted import (`bind: sole_instance`), each tool the draft names is held to the real tool list of the action or MCP server it binds to (`check_drafted_tools` in `app/agents/portable.py`): an action's from the toolset registry for its type, an MCP server's from the tool cache, else one discovery of at most 10 s with the person's sign-in. A tool the list has binds under its real name and full name; one it lacks is left out and listed as `tool` `<action or server>.<tool>` `not_found`; every tool of a list that cannot be read (not signed in, unreachable, too slow) is `unverified`; an action or server left with none of its named tools is not attached (`no_tools`). An MCP server with all its tools, and an action that names none, are not checked. Nothing a model named is created unchecked or dropped unsaid: each reaches the card's `unresolved`. An export imported by name (C4.08) is not checked: it carries a real agent's tools.

## Deviations

- A collection's folders are not drafted: a portable agent carries knowledge by connected app; the person picks folders in the agent's settings once it exists.
- The probe does not count an open server's tools (that needs a full session handshake).
- Members list only the skill proposals their own runs learned (admins all).
- A member could register as a category a name only skills they cannot see carry, then rename it; it changes a label, never a skill.
- The card's options are English, as the question card's are; the draft itself is in the person's language and the app writes the card's controls in the reader's.

## Owner amendments

None.

## Status

**Accepted** (2026-11-09), UI-B4.

**Partly set aside** (2026-11-10, owner: the new interface waits). UI-ASIDE removed from `harness/v2-mainsync` what
only the new interface used: `propose_agent` and its proposals, an agent's own approval rule, the org's skill
categories (`harness_0013`), the tools' read or write class on listings and the MCP address check. The skills
changes (approve with edits, the learner decides, change notes, rename, the learning agent recorded) stay: the classic
skill review uses them. The rest lives on `harness/ui-revamp`.
