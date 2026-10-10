import { expect } from 'chai';
import { explainChat } from '../../../src/modules/authz/domain/explain';
import { chatRole, isUnresolved, messageRole } from '../../../src/modules/authz/domain/rules';
import { ChatFacts, Subject } from '../../../src/modules/authz/domain/types';

const subject = (teamIds: Subject['teamIds'] = []): Subject => ({ userId: 'u1', orgId: 'o1', teamIds });
const chat = (over: Partial<ChatFacts> = {}): ChatFacts => ({
  orgId: 'o1',
  ownerId: 'owner',
  sharedWith: [],
  projectId: null,
  projectVisibility: 'private',
  project: null,
  ...over,
});

describe('authz rules (H1-H3, explain); truth tables live in the golden matrix', () => {
  it('H3 message role equals chat role', () => {
    expect(messageRole('editor')).to.equal('editor');
  });

  it('H1 never calls into team data when a direct row already satisfies the requirement', () => {
    const teamIds = new Proxy([] as string[], {
      get() {
        throw new Error('teams consulted');
      },
    });
    const facts = chat({ sharedWith: [{ userId: 'u1', accessLevel: 'read' }, { teamId: 't1', accessLevel: 'write' }] });
    const result = chatRole(facts, subject(teamIds), { collab: true });
    expect(isUnresolved(result)).to.equal(false);
  });

  it('explain lists every path and flags unresolved teams', () => {
    const facts = chat({
      sharedWith: [{ userId: 'u1', accessLevel: 'read' }, { teamId: 't1', accessLevel: 'write' }],
    });
    const resolved = explainChat(facts, subject(['t1']), { collab: true });
    expect(resolved.role).to.equal('editor');
    expect(resolved.via.map((p) => p.type)).to.deep.equal(['direct', 'team']);
    const unresolved = explainChat(facts, subject('unresolved'), { collab: true });
    expect(unresolved.via.map((p) => p.type)).to.deep.equal(['direct']);
    expect(unresolved.teamsUnresolved).to.equal(true);
  });

  describe('unresolved teams and the project path (H2)', () => {
    const projectFacts = (members: Array<{ principalType: 'user' | 'team'; principalId: string; role: string }>, projectChatAccess?: string) => ({
      orgId: 'o1',
      ownerId: 'someone',
      visibility: 'private' as const,
      members,
      projectChatAccess,
    });
    const inProject = (members: Parameters<typeof projectFacts>[0], access?: string): ChatFacts =>
      chat({ projectId: 'p1', projectVisibility: 'project', project: projectFacts(members, access) });

    it('is unresolved when only a project team membership could grant', () => {
      const facts = inProject([{ principalType: 'team', principalId: 't1', role: 'viewer' }]);
      expect(isUnresolved(chatRole(facts, subject('unresolved'), { collab: true }))).to.equal(true);
      expect(isUnresolved(chatRole(facts, subject('unresolved'), { collab: false }))).to.equal(true);
    });

    it('is not unresolved when the project ceiling keeps the team from reaching the needed role', () => {
      const facts = inProject([{ principalType: 'team', principalId: 't1', role: 'editor' }]);
      const result = chatRole(facts, subject('unresolved'), { collab: true, needed: 'editor' });
      expect(isUnresolved(result)).to.equal(false);
    });

    it('is not unresolved when the project has no team members or the chat is not project-visible', () => {
      const noTeams = inProject([{ principalType: 'user', principalId: 'other', role: 'viewer' }]);
      expect(isUnresolved(chatRole(noTeams, subject('unresolved'), { collab: true }))).to.equal(false);
      const priv = chat({ projectId: 'p1', project: projectFacts([{ principalType: 'team', principalId: 't1', role: 'viewer' }]) });
      expect(isUnresolved(chatRole(priv, subject('unresolved'), { collab: true }))).to.equal(false);
    });

    it('resolved teams still grant through the project when the flag is off', () => {
      const facts = inProject([{ principalType: 'team', principalId: 't1', role: 'viewer' }]);
      const result = chatRole(facts, subject(['t1']), { collab: false });
      expect(isUnresolved(result) ? null : result.role).to.equal('viewer');
    });
  });
});
