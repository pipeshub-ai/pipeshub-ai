# ADR-OWD-15 — Data classification as a policy input

## Context

[OWD-15](../one-way-doors.md): does the policy model carry record classification next to taint? A rule keyed on classification cannot be applied afterwards to transcripts and snapshots written without it, so the field has to be in the journal, the labels and the schemas from the start, whatever applies it.

## Decision

Carry classification beside the existing labels (REPORT D15), not as a second taint system: a run carries one opaque classification (`backend/python/harness_core/security/classification.py`), and an unlabelled record never counts as "public".

This repository keeps only the extension points; their defaults do nothing:

- Python `ClassificationPort` (`backend/python/app/harness/classification.py`). Default `NO_CLASSIFICATION`: the org has no classification, model calls pass through unguarded, the run's approval policy is unchanged and no level is recorded.
- Python `ClassificationPort.record_attributes`: what a run records beside a source it reads (`RecordRef.attributes`, extra fields of the provenance event's `RecordSource`/`ConnectorSource`, both `x-extensible`). Default: nothing.
- Connectors' `SourceMetadataReader` (`backend/python/app/connectors/core/base/source_metadata.py`), which writes `Record.edition_fields` (stored as `editionFields`). Default `KEEP_HELD_METADATA`: nothing is read, and a record keeps what it holds.
- Node `HarnessEdition.classification` (`backend/nodejs/apps/src/modules/agent-harness/edition.ts`). Null by default: no conversation level is recorded and the usual approval rule decides.
- Node `RetentionPass` (`backend/nodejs/apps/src/modules/agent-harness/ports/session-retention.ts`, token `AGENT_HARNESS_RETENTION_PASS`). Unbound: the retention sweep makes no extra pass.
- Policy sections an implementation adds come through the contract overlay (`x-extensible`, `contracts/harness/codegen/extensions.py`); without a `PolicySectionRule` such a section is refused `CP_NOT_EXPOSED`.

## As built

Audited at `959c3bda72`. Decision rows: the v2 architecture report D15.

- The run's policy and its ledger share the opaque classification shape (`harness_core/security/classification.py`); it stays unconfigured (`UNCONFIGURED`) without an implementation.
- An org without a classification keeps today's policy object everywhere.
- Passing: `backend/python/tests/unit/harness/policy/test_classification_ports.py` (the do-nothing default), `backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts` (the null default), policy-section-rules.test.ts (moved out of this repository) (a section without a rule refused `CP_NOT_EXPOSED`), `backend/python/tests/unit/connectors/sources/microsoft/behaviour/test_onedrive_sync_behaviour.py` (no source metadata read by default), `backend/python/tests/unit/connectors/core/test_source_metadata.py` (edition fields stored and cleared), `backend/python/tests/unit/harness_core/provenance/test_source_attributes.py` (attributes on the wire and in the ledger).

## Deviations

- Rules that apply a classification (to models, egress, retention or approvals) are not part of this repository; the extension points above are.
- OWD-19's "access ≥ classification" rule for steerers can read the session's classification; it is not wired.

## Owner amendments

- **2026-11-14:** this repository holds only extension points whose default does nothing; the rules that read a classification are not built here.

## Status

**Accepted as amended** (2026-10-29, amended 2026-11-14). The field is carried; applying it is an extension point with no default in this repository. OWD-19's steerer rule is OWD-19's.
