import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { SubjectTeamResolver } from '../../../src/modules/authz/subject-team.resolver';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';

describe('SubjectTeamResolver.forUser', () => {
  it('looks teams up with a one-minute team:ids:read token, not a session token', async () => {
    const callerTeamIds = sinon
      .stub()
      .resolves({ status: 'ok', teamIds: ['t1', 't2'] });
    const issue = sinon.stub().returns('scoped');
    const resolver = new SubjectTeamResolver({ callerTeamIds } as never, {
      issue,
    });
    expect(await resolver.forUser('u1', 'org1')).to.deep.equal(['t1', 't2']);
    expect(issue.firstCall.args).to.deep.equal([
      { userId: 'u1', orgId: 'org1', scopes: [TokenScopes.TEAM_IDS_READ] },
      '1m',
    ]);
    const identity = callerTeamIds.firstCall.args[0];
    expect(identity).to.deep.include({ userId: 'u1', orgId: 'org1' });
    expect(identity.authHeaders).to.deep.equal({ Authorization: 'Bearer scoped' });
  });

  it('is unresolved when the lookup is', async () => {
    const callerTeamIds = sinon.stub().resolves({ status: 'unresolved' });
    const resolver = new SubjectTeamResolver({ callerTeamIds } as never, {
      issue: () => 'scoped',
    });
    expect(await resolver.forUser('u1', 'org1')).to.equal('unresolved');
  });
});
