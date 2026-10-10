import { expect } from 'chai';
import { DecisionLogEntry, SampledDecisionLog } from '../../../../src/modules/authz/decision-log';
import { ConversationAccessAuthorizer } from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.authorizer';
import {
  ConversationNotFoundError,
  ConversationReadOnlyError,
  TeamResolutionUnavailableError,
} from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors';
import { ConversationAccessFields } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types';

const session: ConversationAccessFields = {
  orgId: 'o1',
  userId: 'owner',
  sharedWith: [{ userId: 'u1', accessLevel: 'read' }, { teamId: 't1', accessLevel: 'write' }],
  aclVersion: 4,
};
const base = { conversationId: 'c1', session, flags: { collab: true }, requestId: 'r1' };
const caller = (userId: string, teamIds: string[] | 'unresolved' = []) => ({ userId, orgId: 'o1', teamIds });

const setup = (sampleRate = 0) => {
  const entries: DecisionLogEntry[] = [];
  const log = new SampledDecisionLog({ write: (e) => entries.push(e) }, sampleRate, () => 0.5);
  return { entries, authorizer: new ConversationAccessAuthorizer(log) };
};

describe('ConversationAccessAuthorizer', () => {
  it('throws the typed error for each deny and logs every deny', () => {
    const { authorizer, entries } = setup();
    expect(() => authorizer.assertAllowed({ ...base, op: 'send', caller: caller('u1') })).to.throw(ConversationReadOnlyError);
    expect(() => authorizer.assertAllowed({ ...base, op: 'read', caller: caller('nobody') })).to.throw(ConversationNotFoundError);
    expect(() =>
      authorizer.assertAllowed({ ...base, op: 'send', caller: caller('nobody', 'unresolved') }),
    ).to.throw(TeamResolutionUnavailableError);
    expect(entries.map((e) => e.code)).to.deep.equal([
      'CONVERSATION_READ_ONLY',
      'CONVERSATION_NOT_FOUND',
      'TEAM_RESOLUTION_UNAVAILABLE',
    ]);
    expect(entries[0]).to.include({ subject: 'u1', action: 'send', resource: 'chat:c1', decision: 'deny', aclVersion: 4 });
  });

  it('returns the role and granting path, and samples allows', () => {
    const { authorizer, entries } = setup(0);
    const result = authorizer.assertAllowed({ ...base, op: 'send', caller: caller('nobody', ['t1']) });
    expect(result.role).to.equal('write');
    expect(result.via.map((p) => p.type)).to.include('team');
    expect(entries).to.have.length(0);
    const sampled = setup(1);
    sampled.authorizer.assertAllowed({ ...base, op: 'read', caller: caller('owner') });
    expect(sampled.entries).to.have.length(1);
    expect(sampled.entries[0]).to.include({ decision: 'allow', role: 'owner' });
  });

  it('a failing log sink does not fail the decision', () => {
    const log = new SampledDecisionLog({ write: () => { throw new Error('down'); } }, 1);
    const authorizer = new ConversationAccessAuthorizer(log);
    expect(authorizer.assertAllowed({ ...base, op: 'read', caller: caller('owner') }).role).to.equal('owner');
  });
});
