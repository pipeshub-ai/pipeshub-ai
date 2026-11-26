/**
 * The configuration-change approval target this tree keeps for a bound
 * approval workflow (learned skill candidate), and the approval refusals'
 * problem bodies. A target applies the decided change only as an external
 * effect of the decision's transaction, so a decision that does not commit
 * never reaches the skill store.
 */
import { expect } from 'chai';
import {
  ApprovalForbiddenError,
  ApprovalInvalidError,
  ApprovalSupersededError,
  ApprovalTargetMoved,
} from '../../../src/modules/control-plane/domain/approval-errors';
import { candidateTargetId } from '../../../src/modules/control-plane/domain/approval-targets';
import { SkillCandidateApprovalTarget } from '../../../src/modules/control-plane/modules/skills/candidate-approval-target';
import type { Applied, CpTx } from '../../../src/modules/control-plane/ports/transaction';
import { refusalOf } from '../../../src/modules/control-plane/transport/cp-route';
import { anApproval, ORG_A } from './support/cp-objects';

/** A transaction that holds each external effect until `commit`, as the audited transaction runs them. */
class HeldTx implements CpTx {
  readonly orgId = ORG_A;
  readonly headRevision = 4;
  readonly session = undefined;
  readonly effects: Array<() => Promise<void>> = [];

  enlist(write: () => Promise<void>): Promise<void> {
    return write();
  }

  external<R>(effect: () => Promise<R>): Applied<R> {
    const applied: { value?: R } = {};
    this.effects.push(async () => {
      applied.value = await effect();
    });
    return applied as Applied<R>;
  }

  async commit(): Promise<void> {
    for (const effect of this.effects) await effect();
  }
}

describe('configuration-change approval targets', () => {
  describe('learned skill candidate', () => {
    const decided: string[] = [];
    const target = new SkillCandidateApprovalTarget({
      approve: async (id) => {
        decided.push(`approve:${id}`);
        return 'published';
      },
      reject: async (id) => {
        decided.push(`reject:${id}`);
        return 'discarded';
      },
    });
    const request = anApproval({
      target: { type: 'catalog-item', id: candidateTargetId('cand-7'), revision: 0 },
      change: { type: 'publish' },
    });

    beforeEach(() => decided.splice(0));

    it('is a catalog item published by the org’s publish rule', () => {
      expect(target.type).to.equal('catalog-item');
      expect([...target.changes]).to.deep.equal(['publish']);
    });

    it('knows only skill candidate ids: any other catalog item is not its target', async () => {
      expect(await target.current(ORG_A, candidateTargetId('cand-7'))).to.deep.equal({
        revision: 0,
        owner: 'org',
        kind: 'skill',
      });
      expect(await target.current(ORG_A, 'skill:published-1')).to.equal(null);
    });

    it('an approval publishes the candidate named without its prefix, after the commit', async () => {
      const tx = new HeldTx();
      const applied = await target.apply(tx, request, { state: 'approved', subset: null });
      expect(decided).to.deep.equal([]);
      await tx.commit();
      expect(decided).to.deep.equal(['approve:cand-7']);
      expect(applied.value).to.equal('published');
    });

    it('a rejection discards the candidate, after the commit', async () => {
      const tx = new HeldTx();
      const applied = await target.apply(tx, request, { state: 'rejected' });
      await tx.commit();
      expect(decided).to.deep.equal(['reject:cand-7']);
      expect(applied.value).to.equal('discarded');
    });
  });

  describe('approval refusals as problem bodies', () => {
    it('a person who may not decide is CP_FORBIDDEN (403), with the reason kept for the log', () => {
      const error = new ApprovalForbiddenError('Only an org admin may approve this', 'NOT_AN_APPROVER');
      expect(error.name).to.equal('ApprovalForbiddenError');
      expect(error.reason).to.equal('NOT_AN_APPROVER');
      expect(refusalOf(error)?.problem).to.include({
        code: 'CP_FORBIDDEN',
        status: 403,
        message: 'Only an org admin may approve this',
      });
    });

    it('a target that changed since the request is CP_APPROVAL_SUPERSEDED (409), not retryable', () => {
      const error = new ApprovalSupersededError('The manifest changed');
      expect(error.name).to.equal('ApprovalSupersededError');
      expect(error.retryable).to.equal(false);
      expect(refusalOf(error)?.problem).to.include({ code: 'CP_APPROVAL_SUPERSEDED', status: 409 });
    });

    it('a change no handler applies is CP_INVALID_CONFIG (422)', () => {
      const error = new ApprovalInvalidError('No handler for this change');
      expect(error.name).to.equal('ApprovalInvalidError');
      expect(refusalOf(error)?.problem).to.include({ code: 'CP_INVALID_CONFIG', status: 422 });
    });

    it('a handler’s “target moved” is not itself a refusal: the workflow turns it into a superseded request', () => {
      const moved = new ApprovalTargetMoved('hub digest changed');
      expect(moved).to.be.instanceOf(Error);
      expect(moved.name).to.equal('ApprovalTargetMoved');
      expect(moved.message).to.equal('hub digest changed');
      expect(refusalOf(moved)).to.equal(null);
    });
  });
});
