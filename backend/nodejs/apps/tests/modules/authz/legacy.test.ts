import { expect } from 'chai';
import { LEGACY_REQUIREMENTS, legacyAllows } from '../../../src/modules/authz/domain/legacy';
import { ConversationOperation } from '../../../src/modules/enterprise_search/services/conversation/domain/types';

const OPERATIONS: ConversationOperation[] = [
  'read',
  'feedback',
  'leave',
  'archiveSelf',
  'send',
  'cancel',
  'regenerate',
  'resume',
  'invite',
  'manageSharing',
  'settings',
  'transfer',
  'rename',
  'linkProject',
  'fork',
  'delete',
];

const path = (type: 'owner' | 'direct' | 'project') => [{ type, ref: 'x', role: 'viewer' as const }];

describe('LEGACY_REQUIREMENTS (the default conversation policy)', () => {
  it('has a row for every operation, for both kinds', () => {
    for (const kind of ['chat', 'agent'] as const) {
      expect(Object.keys(LEGACY_REQUIREMENTS[kind]).sort()).to.deep.equal([...OPERATIONS].sort());
    }
  });

  it('chat: read is O,R,P; feedback is O,R; everything else is owner only', () => {
    expect(legacyAllows('chat', 'read', path('project'))).to.equal(true);
    expect(legacyAllows('chat', 'read', path('direct'))).to.equal(true);
    expect(legacyAllows('chat', 'feedback', path('direct'))).to.equal(true);
    expect(legacyAllows('chat', 'feedback', path('project'))).to.equal(false);
    for (const op of ['send', 'cancel', 'rename', 'delete', 'archiveSelf', 'regenerate', 'fork'] as const) {
      expect(legacyAllows('chat', op, path('owner'))).to.equal(true);
      expect(legacyAllows('chat', op, path('direct'))).to.equal(false);
      expect(legacyAllows('chat', op, path('project'))).to.equal(false);
    }
  });

  it('agent: read is O,D,P (a direct recipient can open it read-only); everything else is owner only', () => {
    expect(legacyAllows('agent', 'read', path('project'))).to.equal(true);
    expect(legacyAllows('agent', 'read', path('direct'))).to.equal(true);
    expect(legacyAllows('agent', 'feedback', path('direct'))).to.equal(false);
    expect(legacyAllows('agent', 'cancel', path('project'))).to.equal(false);
    expect(legacyAllows('agent', 'send', path('owner'))).to.equal(true);
  });

  it('denies when there is no path at all', () => {
    expect(legacyAllows('chat', 'read', [])).to.equal(false);
  });
});
