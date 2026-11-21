# ADR-UI-03: An admin list names what it holds in its own answer

- Package: R30-LAT (STATUS QAFINAL-FU-VOL, admin and performance pages).
- Related: ADR-UI-01 (a person's run list names its agents and chats the same way), WP-51 (the query service's
  internal agent names), ADR-OWD-14 (agent versions and the schedule pin), G-41 (the `access` policy section).
- Finding it answers: `frontend/tests/e2e/perf/volume-budgets.json`, pre-registered before any measurement:
  `schedules` (300 rows), `approval-policy` (100 agents and groups) and `ai-access` (100 rules naming groups) each
  allow `extraRequests: 0`, no more API requests with hundreds of items than with three.

## Context

The admin pages hold agents by key and groups by id, and the browser named them, one request per id:

- `ApprovalsDirectory.names` (`frontend/app/(main)/workspace/approvals/directory.ts`) sent one
  `GET /api/v1/agents/:key` per agent and one `GET /api/v1/userGroups/:id` per group; only users were batched
  (`POST /users/by-ids`). The schedules page, the approval policy and AI access's "Who can use agents" called it.
- Each schedule row (`ScheduleVersion`) sent `GET /api/v1/agents/:key/versions` to learn the version live now for
  its "Version N is live" badge and its choices, with no dedupe: 300 rows, 300 requests.

The shared fault: the Node answers that hold the keys did not carry what the page shows of them, so every page made
up for it with its own per-id requests. Batching each page in the browser would only move the fault: a new page
would repeat it, and the browser cannot batch agent names (no client route does).

## Decision

D1. **The answer that holds an id names it.** A Node view that holds agent keys or group ids returns their display
data, resolved in one batched lookup per kind per answer, whatever the list's length:

- `GET /api/v1/agent-harness/schedules`: each schedule carries `agentName` (string or null) and `agentLiveVersion`
  (integer from 1, or null), from one `AgentSummaryReader.summaries` call for the page of schedules.
- `GET` and `PUT /api/v1/agent-harness/admin/approvals`: the view carries
  `names: {agents: {key: name}, groups: {id: name}}` for the agents and groups its rules hold
  (`AdminListNames.approvalPolicy`).
- `GET /api/v1/control-plane/policy/org/current/access`: the section carries `names: {groups: {id: name}}` for every
  group its value holds (`PolicyAdmin.read`, `accessGroups.groupNames`).

D2. **One lookup each, on the store's own batched read.** Agent names and live versions come from the query
service's internal names route (`POST /api/v1/agent/internal/names`), which with `include: ["latestVersion"]` also
answers `latestVersions: {key: n}`, each named agent's latest published version (`AgentVersionStore.numbers`; read
only, so an admin list never runs the version-1 migration). The include is backwards compatible both ways: an older
query service ignores it and Node reads the versions as null; an older Node never asks. Group names come from the
group repository's new `listLiveByIds` (one `$in` query on MongoDB, one `= ANY` query on PostgreSQL), through the
harness group directory (`groupNames`) that `access` already checks ids against.

D3. **A failed lookup never fails the answer.** The list or view is still answered; names and versions are null or
absent, logged once per answer (`schedule.agent_names_failed`, `admin.names_failed`). The page shows what it showed
for an id it could not name: the key, "A group that no longer exists", and no "Version N is live" badge (the
choices are keep or always latest).

D4. **The browser asks per id only for what no answer holds.** The pages read names and versions from those
answers. `ApprovalsDirectory.names` stays for people (one batched request) and for ids no answer holds; a group or
agent the admin just picked already has its name from the search it was picked from, so nothing is named again.

## As built

- Python: `app/harness/agent_names.py` (`latest_versions`), `app/api/routes/agent.py`
  (`AgentNamesRequest.include`, the route's `latestVersions`).
- Node: `ports/agent-names.ts` (`AgentSummaryReader`), `adapters/harness-agent-names.ts` (`summaries`),
  `application/schedule-service.ts` (`ScheduleListItem`, `list`), `application/admin-list-names.ts`,
  `ports/group-access.ts` (`GroupNameReader`), `transport/admin-routes.ts`, `binders/policy-settings.ts`
  (`AGENT_HARNESS_ADMIN_LIST_NAMES`), control-plane `application/policy-admin.ts` (`SectionContext.names`),
  `libs/repositories/group` (`listLiveByIds`), `libs/repositories/agent-harness/group-membership` (`groupNames`).
- Contract: `pipeshub-openapi.yaml` (`AgentSchedule.agentName` and `agentLiveVersion`, the access section's `names`).
- Frontend: `schedules` (`toSchedule`, `ScheduleVersion` takes `live` from the schedule, the page names only owners
  and only in the org view), `approvals/api.ts` (`names`), `approval-policy.tsx`, `harness-ui/control-plane-api.ts`
  (`PolicySection.names`, kept across a save), `ai-access/components/group-access-section.tsx`.
- Tests: `tests/modules/agent-harness/adr-ui-03-admin-list-names.test.ts` (one lookup at 3 and 150 schedules, at
  100 agent rules, at 101 access groups; a failed lookup still answers), `harness-agent-names.test.ts`,
  `tests/unit/api/routes/test_agent_names_route.py`, the group repository's contract case, and the pages' vitest.

## Deviations

- The org view's schedule badge: the list carries the live version for every row, so an admin now also sees
  "Version N is live" on another person's pinned schedule (read only). It was hidden only because the row would have
  cost a request.
- The live version is read for the org, not checked against the schedule owner's own access to the agent's
  versions; a schedule already names the agent, and its firings check the owner's access at each run.
- Other pages that still named agents one request per key (`memory-policy`, `unknown-effects`) were not in the
  pre-registered budget. R31-LAT gave them one (`memory-policy`, `unknown-effects`: 13 -> 111 requests at 100
  agents before) and they follow D1 now: `GET /admin/memories` and `GET /admin/unknown-effects` carry
  `names.agents` (`transport/held-names.ts`, openapi `HeldAgentNames`), 11 -> 11 after (VOL-9, VOL-10).

## Owner amendments

None yet.

## Status

**Accepted** 2026-11-12: decided from first principles, long term, UX-first.
