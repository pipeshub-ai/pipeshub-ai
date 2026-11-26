# ADR-OWD-04 — Canonical event envelope and transcript storage

## Context

[OWD-4](../one-way-doors.md) fixes the versioned event envelope and where transcripts live. Transcripts, the UI, audit, the cost ledger, the public API and SSE resume all read it, and historic sessions must stay replayable for years.

## Decision

Adopt OWD-4 with the envelope built in the v2 architecture report D2/D3:

- a typed, versioned, append-only journal in the Node-owned Mongo store is the truth; checkpoints are derived;
- payloads and checkpoints go to blob storage, by content address;
- never the graph DB for event streams;
- readers handle every historical version, and old histories are replayed in CI.

## As built

Audited at `959c3bda72`. Decision rows: REPORT D2, D3, D26, D27, D28 and §6.1.2/§6.1.7.

- `contracts/harness/core/envelope.schema.json` has orgId, sessionId, streamId, a gap-free `seq`, type, a per-type schemaVersion, ts, writer, labels, visibility, and data or payloadRef.
- Passing in the gate:
  - `backend/python/tests/unit/contracts_harness/test_c1_schemas.py` (envelope examples valid, invalid fixtures rejected)
  - `backend/python/tests/contracts/test_replay_compat.py` (64) and `backend/python/tests/contracts/test_journal_histories.py` (81)
  - `backend/python/tests/unit/harness/test_node_journal_contract.py::test_seq_is_gap_free_per_stream_and_streams_are_independent`
  - `backend/nodejs/apps/tests/modules/agent-harness/projection/stream-service.test.ts` "L-1: a reconnect mid-message with Last-Event-ID continues with no gap and no duplicate"

## Deviations

Accepted divergences (the replay and resume suites above pass on the built shape):

1. **Taxonomy.** The envelope uses PipesHub's own typed event taxonomy rather than ACP `session/update`. ACP is mapped at the adapter edge ([ADR-OWD-11](ADR-OWD-11-adapter-contract.md)), so the journal does not depend on one protocol's evolution.
2. **`runId` instead of `turn_id`.** A run is one turn ([ADR-OWD-05](ADR-OWD-05-durable-run-and-session.md)).

To change:

3. **Native transcripts.** Native engine state is kept as a sealed `engine.checkpointed` blob, not as a verbatim per-turn transcript that can be parsed again. The raw native stream is sealed per turn, and `native.unknown` events are journalled, in WP-03 JOURNAL-HARDEN (C2.31, C2.39; the project tracker Plan, Wave 1).

## Owner amendments

None.

## Status

**Accepted** (2026-10-26). Built and tested. The verbatim native stream lands in WP-03.
