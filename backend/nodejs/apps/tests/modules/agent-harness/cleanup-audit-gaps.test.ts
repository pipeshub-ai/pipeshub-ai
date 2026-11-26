/**
 * CLEANUP-G: the data-cleanup gaps G1 to G7 a cleanup audit found. Each is
 * proven where it lives: G1 and G2 by principal-erasure.mongo.itest.ts (the
 * census of a deleted org and user), G3 by the schedule-store contract and
 * the agent-erasure itest, G4 and G5 by the erasure-census itest and
 * unsent-attachments.test.ts, G6 by run-sweeper-reap.test.ts and the broker's
 * reconcile, G7 by the query service's real-graph erasure. What is pinned
 * here is the shape those proofs rest on.
 */
import { expect } from 'chai';
import {
  AGENT_DATA_STORES,
  PRINCIPAL_DATA_STORES,
} from '../../../src/modules/agent-harness/domain/session-erasure';
import {
  ORG_ERASURE_STEPS,
  USER_ERASURE_STEPS,
} from '../../../src/modules/agent-harness/domain/principal-erasure';

describe('CLEANUP-G: cleanup gaps as decided', () => {
  it('CLEANUP-G0: the principal stores user and org deletion empty, and what agent deletion reaches', () => {
    expect(PRINCIPAL_DATA_STORES.map((entry) => entry.store)).to.deep.equal([
      'modelAccounts',
      'agentSchedules',
      'agentMemories',
      'engineSignIns',
      'inbox',
      'personalInstances',
    ]);
    // The org's principal stores are emptied by the hooks the erasure job holds.
    for (const entry of PRINCIPAL_DATA_STORES) {
      expect(entry.callers, entry.store).to.include('modules/agent-harness/binders/erasure.ts#orgPrincipalErasers');
    }
    expect(AGENT_DATA_STORES.map((entry) => entry.store)).to.deep.equal(['agentMemories', 'agentSchedules']);
  });

  it("CLEANUP-G1: an org's runs stop first, then it is erased session by session, then its own data, then its keys; its audit chain goes last, after its retention", () => {
    const at = (step: (typeof ORG_ERASURE_STEPS)[number]) => ORG_ERASURE_STEPS.indexOf(step);
    expect(ORG_ERASURE_STEPS.slice(0, 3)).to.deep.equal(['cancel_runs', 'end_sandboxes', 'org_event']);
    for (const step of ['sessions', 'await_sessions', 'query_service', 'sandbox_folders', 'principals', 'org_stores', 'redis'] as const) {
      expect(at(step), step).to.be.greaterThan(at('org_event'));
      expect(at(step), step).to.be.lessThan(at('data_keys'));
    }
    expect(ORG_ERASURE_STEPS.slice(-3)).to.deep.equal(['data_keys', 'audit_retention', 'audit']);
  });

  it("CLEANUP-G2: a deleted user's job journals the conversations moved to an admin, erases the uploads they never sent and their cost rows (C9.06)", () => {
    expect([...USER_ERASURE_STEPS]).to.deep.equal(['journal_transfers', 'unsent_uploads', 'control_plane', 'cost_ledger', 'engine_choices']);
  });
});
