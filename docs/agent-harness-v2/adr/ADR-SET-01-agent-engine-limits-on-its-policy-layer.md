# ADR-SET-01: An agent's engine limits, budgets and model routes are its policy layer's

- Package: R31-SET (STATUS C411-FU-2, part of C4.11).
- Related: ADR-OWD-09 (policy and approvals: one resolver, narrow only, a widening refused naming the layer),
  ADR-OWD-14 (agent versions), E4a (engine profiles), ENGPICK (an agent's coding engine on its definition), C4.08
  (an agent's policy layer moves with it).

## Context

R30-REL recorded where every org- and agent-exposed policy dimension is set
(setting-surfaces.ts (moved out of this repository)). Twenty-one agent-layer rows had no page: the
agent's engine profile narrowing (mode, sandbox runtime, network, tools, limits, sub-agents, reasoning effort,
engine approvals, skills), `budgets.*`, `models.routes` and `models.roleCostCeilings`. They could be set in two
stores by API:

- the agent's **definition** (the versioned agent document), whose `engineProfile` the builder's Coding engine card
  writes (its engines and model accounts), and which admission read as the agent's engine narrowing;
- the agent's **policy layer** (`agent:<key>` in the policy store), which "Run settings for this agent" writes and
  the run's pin folds below the org.

The admin API refused every `engineProfile.*` write at any scope ("changed on its own page"), because the org's
engine profile lives in the KV engine-profile document; so an agent's engine limits could only go in its
definition, beside the owner's choices, while its budgets and routes went in its policy layer. One editor had to
pick a store per row first.

## Decision

D1. **Governance limits are the agent's policy layer, never its definition.** The card "Engine limits for this
agent" writes the agent's own policy sections `engineProfile`, `budgets` and `models`, the layer "Run settings for
this agent" writes, on the same routes (`GET`/`PUT /api/v1/control-plane/policy/agent/{key}/{section}`, If-Match,
one audited control-plane write). These rows may only narrow the org's profile, so they belong with the other
limits an admin sets on the agent: one layer holds the agent's precedence and its audit, and they move with the
agent (C4.08) as its other sections do. A definition version is the owner's content, published and rolled back
with the agent's prompt; a limit must not roll back with it.

D2. **What stays on the definition.** The engines an agent may run (`engineProfile.engines`) and the model accounts
it may use (`engineProfile.accountKinds`) stay on its definition: they are the agent's choice of executor, edited on
its Coding engine card, and which engine it runs is part of a version. `engineProfile.access` and `resultSchema` are
the host's. The admin API refuses these four on an agent's policy layer as set elsewhere (`CP_NOT_EXPOSED`).

D3. **Store-only at an agent's scope.** No KV document holds an agent's engine limits, so at an `agent:` scope the
other `engineProfile` paths are store-only (`AGENT_STORE_ONLY_PATHS` in
store-only-dimensions.ts (moved out of this repository)): the admin API writes them, and in
`CONTROL_PLANE_POLICY_STORE=dual` the snapshot lays them over the agent's KV layer as it does budgets. At the org's
scope `engineProfile` stays the engine-profile document's (AI Access › Engine profile).

D4. **Narrow only, as the server checks.** A save is refused by the one resolver when it asks for more than the
layers above (the release and the org): `CP_WIDENING`, whose alias is `AGENT_HARNESS_ENGINE_PROFILE_WIDENING`,
naming the dimension and the `agent` layer. The card shows each row's ceiling from the section read and refuses the
same widenings before the server, reading each row's order from the contract's `policy-dimensions.json` (a rank
may only drop, a raised rank only rise, a tree value narrow to its children, a flat value to itself or its bottom).

D5. **The engine authority admission offers is the one the run is pinned to.** Admission's engine profile step
resolves release defaults under the org ceiling, then the agent's policy layer, then its definition, then the
request. The agent's layer comes from the snapshot the turn was routed on (`context.routed.basis`), the snapshot the
pin folds, so the step makes no read of its own. A definition that asks for more than the agent's limits withholds
`code_task` with `engine_profile_widening` naming the `agent` layer, as any narrowing above the definition does.

## As built

- Node: `AGENT_STORE_ONLY_PATHS` and a scope-aware `isStoreOnly`/`withStoreOnly`
  (store-only-dimensions.ts (moved out of this repository)); `PolicyAdmin.write` and
  `assertAdmissible` check writability for the scope
  (policy-admin.ts (moved out of this repository)); `resolveEngineProfile` takes an
  `agentPolicy` layer between the org and the definition
  (`backend/nodejs/apps/src/modules/agent-harness/domain/engine-profiles.ts`); `EngineProfileStep` folds the routed
  snapshot's agent layer (`backend/nodejs/apps/src/modules/agent-harness/application/admission/policy-steps.ts`).
  Tests: c411-fu2-agent-engine-limits.test.ts (moved out of this repository) SET-1..SET-6.
- Frontend: `AGENT_ENGINE_LIMITS` in run-settings-form.ts (moved out of this repository) (the same
  spec-driven form as Run settings, now with dotted keys and the `level`, `names`, `subAgents` and `costMap` field
  kinds), `AgentEngineLimits` beside `AgentRunSettings` in
  agent-run-settings.tsx (moved out of this repository), ten locales. Tests:
  agent-engine-limits.test.tsx (moved out of this repository) SET-UI-1..6;
  `setting-surfaces.test.ts` C411-SS-4 (no agent-layer row is a gap).

## Deviations

- With `CONTROL_PLANE_PIN` off or shadow a run is not pinned and today's resolution never read an agent's policy
  layer; the agent's engine limits apply from `on` (the default since C4.04), as its budgets and routes do.
- Lowering the org's ceiling below a limit an agent already saved refuses that agent's runs at the pin, naming the
  dimension, until the agent's limit is lowered: the resolver never clips a widening (principle 6), as for every
  other agent-layer row.

## Owner amendments

None.

## Status

**Accepted** (R31-SET, 2026-11-12: the card writes the agent's policy
layer, not its versioned definition).
