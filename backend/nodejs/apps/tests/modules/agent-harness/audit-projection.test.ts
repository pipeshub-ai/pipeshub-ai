import { expect } from 'chai';
import { auditProjection } from '../../../src/modules/agent-harness/domain/audit';

describe('agent-harness audit projection', () => {
  it('session.erased keeps who, why and the step counts; never anything else', () => {
    const projection = auditProjection('session.erased', {
      by: 'user-1',
      reason: 'user_deleted',
      counts: { journal: 12, payloads: 3, note: 'not a count' },
      sessionTitle: 'must not be audited',
    });
    expect(projection?.fields).to.deep.equal({
      by: 'user-1',
      reason: 'user_deleted',
      count_journal: 12,
      count_payloads: 3,
    });
    expect(projection?.dataDigest).to.match(/^sha256:[0-9a-f]{64}$/);
  });

  it('session.erased without counts audits who and why only', () => {
    expect(
      auditProjection('session.erased', { by: 'system', reason: 'retention', counts: null })?.fields,
    ).to.deep.equal({ by: 'system', reason: 'retention' });
  });

  it('AU-1 (CPD-1): interaction.granted is chained with the interaction and the scope only', () => {
    expect(
      auditProjection('interaction.granted', {
        interactionId: 'int-1',
        scope: 'conversation',
        note: 'not audited',
      })?.fields,
    ).to.deep.equal({ interactionId: 'int-1', scope: 'conversation' });
  });

  it('audits exactly the catalog types, and never an inherited property name', () => {
    for (const type of ['session.erased', 'run.completed', 'tool.reconciled', 'interaction.resolved']) {
      expect(auditProjection(type, {}), type).to.not.equal(null);
    }
    for (const type of ['model.request', 'x.pipeshub.answer.finalized', 'constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(auditProjection(type, { text: 'content' }), type).to.equal(null);
    }
  });
});
