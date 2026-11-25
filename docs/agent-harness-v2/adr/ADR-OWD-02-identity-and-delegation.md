# ADR-OWD-02 — Identity and delegation ("on whose behalf")

## Context

[OWD-2](../one-way-doors.md) settles who a session acts as, how that identity reaches every tool call, and how it shows in audit. Audit records, ACLs, approval routing and artifact permissions all encode the answer, so changing it later re-reads history.

## Decision

Adopt OWD-2. Every run has one principal: a user, or an agent service account. It also records an initiator (the human or trigger that started it) and an agent identity. Every tool call is attributed as `(org, principal, initiator, agent, session, harness@version)`. Retrieval runs as the principal. IDs in tool arguments are never trusted. Children inherit the principal and can only narrow it.

Spec item amended here (UX-first, 2026-10-26): **C3.24 / KN-09** — a record the principal cannot read answers exactly like a missing one, so an answer never reveals that a record exists. "No access" is not a distinct message; "index not ready" still is.

## As built

Audited at `959c3bda72`. Decision rows: the v2 architecture report D10, D11, D21 and §6.13; P2.13 knowledge publications.

- The RunSpec carries `principalRef`, and `x.pipeshub.via` is derived from verified auth only: `backend/nodejs/apps/src/modules/agent-harness/transport/request-identity.ts`.
- Knowledge access is actor ∩ agent grant (D21). A child's grant is a subset of its parent's (D10, `inheritedGrants`).
- Passing in the gate:
  - `backend/python/tests/unit/harness/gateway/test_ph_contract.py` (27 tests, including "credential in arguments")
  - `backend/python/tests/unit/harness_core/engines/durable/test_children.py::test_ch1_an_engine_child_narrows_its_parents_engine_and_is_pinned_with_its_digest`
  - `backend/python/tests/unit/harness/policy/test_policy_properties.py::test_tgw18_a_child_inherits_its_parents_taint_at_the_spawn`

### Amendment D-ATT-1 (R34-FIX, 2026-11-13): a file attached in a service-account agent's chat is read as the attacher's

**Fault (R33JGAPS-FU-6).** A member attached a file in a chat with a service-account agent; the run's goal said "User
attached file(s): …" with no text (`run.attachments resolved=False`), no notice named the file, and the answer read
complete. Admission had already checked the file as the person who sent the turn (`keep_accessible_attachments` with
the run's actor), but every reader after it (the goal's resolver, the history replay, the first-turn rehydration,
legacy's intent routing) checked it again as the run's acting identity, which in such a chat is the agent's service
account. A service account holds only the org's grant on its own uploads (`ORGANIZATION` permission edge), never the
member's, so the read was refused in the one place nobody reported it.

**Decision (first principles).** Attaching a file to a conversation is the attacher's grant to the run they start
in it: such a file is readable by that run. One rule, in one place (`attachment_reader_id`,
`app/utils/record_access.py`): an attachment read is authorised as the person behind the turn (`callerUserId`, the
requesting user a service-account chat already records for knowledge publications), falling back to the acting
identity only when no person stands behind the request (the Slack bot's own upload, still granted by the org edge).
Every attachment reader passes through it; no reader keeps its own copy of the identity choice.

- **Never wider.** Only the attachments of this conversation reach the resolvers, each checked live against the
  attacher's own access: another person's file stays unreadable, a revoked or trashed file stays unreadable, and the
  service account's other runs (another person chatting with the same agent) carry their own caller, who cannot read
  it. The account gains no standing grant, nothing is written to the graph, and retrieval and tool reads keep the
  agent's own scope.
- **Never silent.** A file of the turn that still does not reach the model (the access check left it out, or its
  content could not be read) leads the answer with ADR-RUN-03 D5's could-not-do line: "I couldn't read the attached
  file notes.txt: access to it was refused." / "…: its content could not be read." Typed as `attachment.not_accessible`
  and `attachment.unreadable` with `file` (`could-not-do.schema.json`), worded by the web chat in ten locales, sent
  typed only to a Node that names `attachment_limits` on its claims; an older Node gets the English line only.
  "the attached file", never "your file": a shared chat's viewer did not attach it (D5's "words true for every
  reader"). A flow rule that refuses such a read can add a further reason.
- **Rebuilt per attempt, not journaled.** The resolution reruns on every attempt (its images and citation maps
  already do), so the list of unread files is rebuilt with it; nothing new enters the fold.

Tests: `backend/python/tests/unit/harness/test_r34fix_service_account_attachments.py` (the attacher's file is read,
another person's file and another caller's run are refused, a bot request keeps only the account's own uploads, the
unread list and the answer's line, the contract), `backend/nodejs/apps/tests/modules/agent-harness/r34fix-attachment-limits.test.ts`,
`frontend/app/(main)/chat/__tests__/r34-attachment-limits.test.tsx`; journey `J-agent.quick-service-attach`.

## Deviations

These parts are missing, and no recorded decision covers them, so **the code changes**, in WP-07 PRINCIPALS + SHARED-WS (Wave 2):

- There is no `onBehalfOf` (D11) and no agent-service principal in `backend/nodejs/apps/src`.
- There is no explicit initiator field. The admitted run records only `via`.
- Service-account principals are not labelled in the UI or in audit; the only marker is `via: service`.

WP-07 adds `onBehalfOf` and initiator to the admitted run and its audit projection, the service-account badge, and a test that a child cannot widen its grant through admission.

## Owner amendments

None.

## Status

**Accepted** (2026-10-26). Built: WP-07 added the initiator, `onBehalfOf` and the principal kind on the admitted run and its `run.admitted` audit record (an audit-only assertion: the journal event workers fold is unchanged, so no worker feature is needed), the child-cannot-widen admission test and the service-agent label in the chat header.
