/**
 * Erasure and deletion jobs in flight and their completion records (R46-CARRY, R45CARRY-FU-2), carried as one unit: a job left
 * behind leaves data that should be erased, and a backfill's plan, items and per-org markers say together which conversations
 * are erased and which org is done. An erasure asked for during the follow arrives as its job's insert and the erased documents'
 * deletes (both applied on PostgreSQL through the key map); data erased on PostgreSQL after the move has no row, so a rollback
 * removes its document from MongoDB instead of bringing it back.
 *
 *  - `agentSessionErasures` -> `harness.session_erasures` (by org and session), `agentErasures` -> `harness.agent_erasures` (by
 *    org and agent): the job in `doc`, the MongoDB id `<org>:<session|agent>` given back at a rollback;
 *  - `agentPrincipalErasures` -> `harness.principal_erasures` (by org and id: the MongoDB id `<org>:org` / `<org>:u:<user>`);
 *  - `agentErasureBackfillPlans` -> `harness.erasure_backfill_plans`: deployment-wide (a plan is the operator's and may cover
 *    every org), keyed by its digest, which is its MongoDB id and the `digest` of the plan in `doc`;
 *  - `agentErasureBackfillItems` -> `harness.erasure_backfill_items` (by org, digest and session; MongoDB id `<digest>:<session>`);
 *  - `agentErasureBackfillMarkers` -> `harness.erasure_backfill_markers` (one per org: the MongoDB id is the org id, which the
 *    PostgreSQL marker keeps as `orgId` in `doc`).
 */
import { docTableCarrier, type DocTableSpec } from './doc-table';

export const ERASURE_TABLES: DocTableSpec[] = [
  {
    collection: 'agentSessionErasures',
    table: 'harness.session_erasures',
    orgPath: 'orgId',
    key: [{ col: 'session_id', path: 'sessionId', kind: 'text' }],
    doc: { omit: [] },
    newId: (doc) => ({
      id: `${String(doc.orgId)}:${String(doc.sessionId)}`,
      oid: false,
    }),
  },
  {
    collection: 'agentErasures',
    table: 'harness.agent_erasures',
    orgPath: 'orgId',
    key: [{ col: 'agent_key', path: 'agentKey', kind: 'text' }],
    doc: { omit: [] },
    newId: (doc) => ({
      id: `${String(doc.orgId)}:${String(doc.agentKey)}`,
      oid: false,
    }),
  },
  {
    collection: 'agentPrincipalErasures',
    table: 'harness.principal_erasures',
    orgPath: 'orgId',
    key: [{ col: 'id', path: '_id', kind: 'text' }],
    idCol: 'id',
    doc: { omit: [] },
  },
  {
    collection: 'agentErasureBackfillPlans',
    table: 'harness.erasure_backfill_plans',
    deployment: true,
    key: [{ col: 'digest', path: '_id', kind: 'text' }],
    idCol: 'digest',
    doc: { omit: [], idAs: 'digest' },
  },
  {
    collection: 'agentErasureBackfillItems',
    table: 'harness.erasure_backfill_items',
    orgPath: 'orgId',
    key: [
      { col: 'digest', path: 'digest', kind: 'text' },
      { col: 'session_id', path: 'sessionId', kind: 'text' },
    ],
    doc: { omit: [] },
    newId: (doc) => ({
      id: `${String(doc.digest)}:${String(doc.sessionId)}`,
      oid: false,
    }),
  },
  {
    collection: 'agentErasureBackfillMarkers',
    table: 'harness.erasure_backfill_markers',
    orgOf: (src) => src.id,
    key: [],
    doc: { omit: [], idAs: 'orgId' },
    newId: (_doc, row) => ({ id: String(row.org_id), oid: false }),
  },
];

export const ERASURE_JOBS = docTableCarrier(
  'erasure',
  'erasure and deletion jobs in flight (session, agent, org and person) and the erasure backfill (plan, items, per-org markers), moved together so nothing that should be erased is left',
  ERASURE_TABLES,
);
