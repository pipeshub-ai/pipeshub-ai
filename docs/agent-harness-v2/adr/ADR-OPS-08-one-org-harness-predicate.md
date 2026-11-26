# ADR-OPS-08: One org predicate decides what an org sees of the harness

- Finding: GATE (2026-11-21). BETA turned the server switch on by default and the Labs flag off by default, and found
  that much of the product keyed on the server switch, so every org on every install got harness behaviour with
  Labs off.
- Amends: [ADR-OPS-06](ADR-OPS-06-one-harness-switch-off-by-default.md) (its 2026-11-20 amendment listed exceptions;
  there are none now). Related: [ADR-OPS-07](ADR-OPS-07-compatibility-profile-for-upgrades.md) (the security profile,
  independent of this), CTL-5 (the kill switch), OWD-14 (agent versions), E7/ORGDEL/CLEANUP-G2 (erasure), J-26.

## Context

The owner's decisions of 2026-11-21: `AGENT_HARNESS_WORKER_ENABLED` defaults to `true`, so every install runs the
harness processes idle; Labs `ENABLE_AGENT_HARNESS` defaults to `false`, and an admin turns the harness on. The
promise: with the Labs flag off, nobody sees or uses the harness and the product behaves as before.

Before the default changed, "the switch is on" and "the org uses the harness" were the same for nearly every install,
so readers took the cheaper one. With the switch on everywhere they diverge, and every reader of the switch that
changes what a person sees leaked: the `/` commands and their menu, the composer's pasted-token check, the agent
builder's versions, export, import and coding-engine section, organisation deletion, the profile's extras, the
control-plane pin on classic turns, erasure instead of soft delete, the internal upload route's 100 MiB cap, the
classic loop's flow guard, agent chats running the latest published version, upload tracking, Continue privately,
participant records on share, and every harness route family.

## Decision

1. **One predicate: the harness is on for an org** when the server switch is on **and** the org's Labs
   `ENABLE_AGENT_HARNESS` is on. Each side reads it in one place:
   - **Node**: `orgHarnessOn(orgId)` in `modules/agent-harness/config.ts`. The rollout policy's `decideForOrg` answers
     it as `harnessOn` (`orgHarnessOnFrom` in `domain/rollout.ts`); the composition records that reader once
     (`recordOrgHarnessReader`, `es.container.ts`). Off when the switch is off, there is no org, no reader is recorded
     or the settings cannot be read.
   - **Python**: `is_agent_harness_on(config_service)` in `app/services/featureflag/platform_settings.py`: the same two
     inputs, the flag read through the config cache (a Labs save invalidates it) and failing closed.
   - **Browser**: `selectAgentHarnessOn` in `frontend/lib/store/feature-flags-store.ts`, from `agentHarness.harnessOn`
     on `GET /platform/feature-flags/effective` (null while the switch is off).
2. **The rule.** Anything that changes product behaviour for a person or an org keys on the org predicate. Process
   and infrastructure start-up (the worker, the gateways, the broker, the listeners, background services, the
   internal routes the worker uses, container bindings) keys on the server switch. Labs keeps listing and saving the
   harness's flags while the switch is on: it is how an admin turns the predicate on.
3. **Readiness, the kill switch and operator stops are not part of the predicate.** They decide whether a new run is
   admitted (`decideOrgRollout`, `useHarness`), and stay there. If they were part of it, an incident stop or a
   journal that is briefly unready would flip what a delete erases, which version of an agent a chat runs and whether
   the composer expands commands, and flip it back afterwards: behaviour nobody chose. It also keeps Python and Node
   reading the same inputs; Python cannot see Node's journal probe. A surface that needs the infrastructure says so
   itself: `continuePrivately` and `resumeInFlightRuns` are the predicate and readiness.
4. **Interdependent parts move together.** Agent versions hidden in the builder means the agent route runs the saved
   agent; no commands in the menu means none expanded on the server; no erasure means the soft delete; the upload cap
   returns to the platform's; the org's classic turns get no pin and are routed by today's decider.
5. **Route families.** `whenHarnessSwitchedOn` mounts each family behind a gate that reads the org a bearer token
   names (without verifying it: it only chooses which router answers, and each router authenticates the request
   itself) and leaves the family with `next('router')` when the org's harness is off, so the request reaches the
   classic routes as on an install with the switch off. `harnessNamespaceWhileOff` answers the namespace's JSON 404
   for such an org. A request that names no org reaches the family, whose authentication answers it.
6. **Org deletion with the predicate off** is the soft delete upstream does, unless the install runs the hardened
   profile, which erases on every delete (ADR-OPS-07). Erasure jobs already queued still finish.
7. **The guarantee is tested per side**: switch on + Labs off ≡ switch off, and switch on + Labs on → the harness's
   behaviour, one row per item (Node `tests/modules/agent-harness/org-harness-matrix.test.ts`, Python
   `tests/unit/harness/test_org_harness_matrix.py`, browser `lib/store/__tests__/harness-org-matrix.test.ts` and the
   component tests that render each surface for the same answers).

## As built

What each reader follows:

| Reader | Follows |
|---|---|
| Worker, gateways, broker, egress proxy (`process_monitor.sh`), Node's internal listener, background services, store preparation, harness Redis and container bindings, the query service's claimer route, the MCP hub for harness runs, `checkInternalUploadLimit` at start, the control-plane pin mode and capability probes, edition routers | the switch |
| Labs listing, saving and `/platform/settings` answering the harness's flags | the switch |
| Admission of a new run | the predicate, readiness, kill switch, operator stops, model families (`decideOrgRollout`) |
| Starting a run in the browser: the composer's harness switch, the default chat's durable Agent mode, the route a turn asks for (`useHarness`, `selectAgentHarnessEnabled`) | admission. With the predicate on and new runs refused, the switch stays, off and disabled, and says new runs are paused |
| Everything a person or an org sees or gets (the table in [HARNESS-OFF.md](../guides/HARNESS-OFF.md)) | the predicate |
| Browser pages, navigation and settings: `HarnessOnly` (AI settings, AI accounts), the workspace sidebar's `agentHarness` gate, Jobs, Needs you and the inbox count, seen marks, Schedules, Memories, the agent builder's gates and environment, the General page's extra cards | the predicate (`selectAgentHarnessOn`), so the kill switch or a stop keeps the page an admin lifts it from |
| The MCP list's `mcpHub` and `mcpToolApproval` (`GET /mcp-servers/my-mcp-servers`) | `MCP_HUB` when set, else the predicate (`is_agent_harness_on`) |
| Reattach, Continue privately | the predicate and readiness |
| Catching up the control plane's grants and installations (`OrgCapabilityRepair`): the capability migration's repair of an org recorded as migrated, run in the background when a Labs save turns the predicate on, at start for every org it is on for, and again on a harness turn after a failure | the predicate's edges: the query service's dual write follows the predicate, so an org that was off holds stale grants until then |
| Run states on conversation lists, a conversation's active run, cancelling a durable run | readiness: they act only on durable runs that exist, so an org that never ran one reads exactly as before |
| Memories' "not saving" notice, the "paused" notes on Schedules and the agent builder's schedules (`selectAgentHarnessPaused`) | admission: the page stays while new runs are refused and says so; a memories route that cannot answer shows its inline "can't be reached" message |

## Deviations

From the predicate the lead first proposed (the switch, readiness, no kill switch and Labs): readiness and the kill
switch stay out of it (decision 3), so behaviour does not flip with an incident stop or a transient journal problem
and Python reads exactly what Node reads.

Consequences:

- One extra settings read (cached by org head when the pin is on) per request under a harness route prefix, and per
  delete, `/` command, internal upload and share. An org with the harness off pays one cached read where it paid
  none with the switch off.
- An org that turns the harness off after using it: its runs in flight finish on the worker (claims never consult the
  rollout) and their answers land in the conversation, but the browser stops reattaching and the run routes are gone
  for it, as with the switch off. Its durable conversations stay readable as messages.
- The kill switch keeps every surface and behaviour of an org that turned the harness on; only new runs stop.

## Owner amendments

None yet. Open for the owner: org-delete erasure with the predicate off on the compatibility profile (decision 6).

## Status

**Accepted** (2026-11-21, GATE). The owner's open question, org-delete erasure with the predicate off on the
compatibility profile, is decided as upstream's soft delete (decision 6) until the owner says otherwise.
