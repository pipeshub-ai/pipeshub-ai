import { expect } from 'chai';
import { LEGACY_REQUIREMENTS, legacyAllows } from '../../../src/modules/authz/domain/legacy';
import { OPERATION_REQUIREMENTS } from '../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy';

const path = (type: 'owner' | 'direct' | 'project') => [{ type, ref: 'x', role: 'viewer' as const }];

describe('LEGACY_REQUIREMENTS (flag off)', () => {
  it('has a row for every operation, for both kinds', () => {
    for (const kind of ['chat', 'agent'] as const) {
      expect(Object.keys(LEGACY_REQUIREMENTS[kind]).sort()).to.deep.equal(Object.keys(OPERATION_REQUIREMENTS).sort());
    }
  });

  it('chat: read is O,R,P; feedback is O,R; everything else is owner only', () => {
    expect(legacyAllows('chat', 'read', path('project'))).to.equal(true);
    expect(legacyAllows('chat', 'read', path('direct'))).to.equal(true);
    expect(legacyAllows('chat', 'feedback', path('direct'))).to.equal(true);
    expect(legacyAllows('chat', 'feedback', path('project'))).to.equal(false);
    for (const op of ['send', 'cancel', 'rename', 'delete', 'archiveSelf', 'regenerate'] as const) {
      expect(legacyAllows('chat', op, path('owner'))).to.equal(true);
      expect(legacyAllows('chat', op, path('direct'))).to.equal(false);
      expect(legacyAllows('chat', op, path('project'))).to.equal(false);
    }
  });

  it('agent: read is O,P (a direct recipient cannot open); everything else is owner only', () => {
    expect(legacyAllows('agent', 'read', path('project'))).to.equal(true);
    expect(legacyAllows('agent', 'read', path('direct'))).to.equal(false);
    expect(legacyAllows('agent', 'feedback', path('direct'))).to.equal(false);
    expect(legacyAllows('agent', 'cancel', path('project'))).to.equal(false);
    expect(legacyAllows('agent', 'send', path('owner'))).to.equal(true);
  });

  it('denies when there is no path at all', () => {
    expect(legacyAllows('chat', 'read', [])).to.equal(false);
  });
});
