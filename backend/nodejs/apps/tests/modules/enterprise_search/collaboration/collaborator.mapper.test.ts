import { expect } from 'chai';
import { Types } from 'mongoose';
import {
  parsePrincipalKey,
  principalKey,
  toCollaborator,
  toStoredCollaborator,
} from '../../../../src/modules/enterprise_search/services/collaboration/domain/collaborator.mapper';

describe('collaborator mapper', () => {
  it('reads a legacy row without principalType as a user', () => {
    const userId = new Types.ObjectId();
    expect(toCollaborator({ userId, accessLevel: 'write' })).to.deep.equal({
      principal: { type: 'user', userId: userId.toString() },
      accessLevel: 'write',
    });
  });

  it('reads a team row and defaults an unknown level to read', () => {
    expect(toCollaborator({ principalType: 'team', teamId: 'all_abc', accessLevel: 'admin' })).to.deep.equal({
      principal: { type: 'team', teamId: 'all_abc' },
      accessLevel: 'read',
    });
  });

  it('returns null for a row with neither id', () => {
    expect(toCollaborator({ accessLevel: 'read' })).to.equal(null);
    expect(toCollaborator({ userId: null, teamId: '' })).to.equal(null);
  });

  it('round-trips through the stored shape', () => {
    const c = { principal: { type: 'team', teamId: 't1' }, accessLevel: 'read', addedBy: 'u9' } as const;
    expect(toCollaborator(toStoredCollaborator(c))).to.deep.equal(c);
    expect(toStoredCollaborator(c)).to.include({ principalType: 'team', teamId: 't1' });
  });

  it('parses and builds principal keys', () => {
    expect(parsePrincipalKey('team:all_0123abcd')).to.deep.equal({ type: 'team', teamId: 'all_0123abcd' });
    expect(parsePrincipalKey('user:u1')).to.deep.equal({ type: 'user', userId: 'u1' });
    expect(parsePrincipalKey('x:1')).to.equal(null);
    expect(parsePrincipalKey('user:')).to.equal(null);
    expect(parsePrincipalKey('nocolon')).to.equal(null);
    expect(principalKey({ type: 'team', teamId: 't1' })).to.equal('team:t1');
  });
});
