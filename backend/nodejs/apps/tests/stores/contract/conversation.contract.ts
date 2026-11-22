/**
 * Contract every ConversationRepository must meet. Store-neutral: the store
 * under test supplies the repository and a unit of work. Pins today's
 * behaviour: ordering and ties, soft deletes, write-member checks, populate,
 * the run lease, seq allocation, and the inputs the archive/redaction views use.
 */
import { expect } from 'chai';
import type { ConversationRepository, NewMessage, NewSession, SessionListQuery, SessionScope } from '../../../src/libs/repositories/conversation';
import type { ScopedUnitOfWork } from './unit-of-work-scope';

export interface ConversationFixture {
  repo: ConversationRepository;
  uow: ScopedUnitOfWork;
  /** True when a failed unit of work rolls back (replica set, PostgreSQL). */
  transactional: boolean;
  /** True when the store removes U+0000 from text before storing it (PostgreSQL cannot hold it). */
  stripsNul?: boolean;
  newId(): string;
}

const CHAT: SessionScope = { kind: 'chat' };
const agent = (agentKey: string): SessionScope => ({ kind: 'agent', agentKey });
const HEX = /^[0-9a-f]{24}$/;

export function describeConversationContract(fx: ConversationFixture): void {
  const { repo, uow } = fx;
  const q = (org: string, user: string, over: Partial<SessionListQuery> = {}): SessionListQuery => ({
    orgId: org,
    userId: user,
    scope: CHAT,
    owned: true,
    shared: false,
    archived: 'no',
    sort: { lastActivityAt: -1, _id: -1 },
    skip: 0,
    limit: 50,
    ...over,
  });
  const userMsg = (content = 'hi'): NewMessage => ({ messageType: 'user_query', content, createdAt: new Date(), updatedAt: new Date() });
  const botMsg = (content = 'ok', over: Partial<NewMessage> = {}): NewMessage => ({
    messageType: 'bot_response',
    content,
    createdAt: new Date(),
    updatedAt: new Date(),
    ...over,
  });
  const open = async (org: string, user: string, over: Partial<NewSession> = {}, first = userMsg()) =>
    repo.openSession(
      {
        orgId: org,
        userId: user,
        initiator: user,
        title: 'chat',
        lastActivityAt: Date.now(),
        status: 'Inprogress',
        sessionType: 'chat',
        ...over,
      },
      first,
    );
  const actor = (org: string, user: string) => ({ orgId: org, userId: user });

  describe('create and read', () => {
    it('openSession returns string ids and stores the first message at seq 1', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user, { modelInfo: { modelKey: 'k', modelName: 'n', modelProvider: 'p', chatMode: 'quick' } });
      expect(s._id).to.match(HEX);
      expect(s.orgId).to.equal(org);
      expect(s.userId).to.equal(user);
      expect(s.initiator).to.equal(user);
      const rows = await repo.listMessages(s._id, { orgId: org });
      expect(rows).to.have.length(1);
      expect(rows[0].dto.seq).to.equal(1);
      expect(rows[0].dto.sessionId).to.equal(s._id);
      expect(rows[0].dto.content).to.equal('hi');
    });

    it('rolls the session back with its first message on a replica set, and not on a standalone server', async () => {
      const org = fx.newId(), user = fx.newId();
      let sessionId = '';
      try {
        await uow.run(async (tx) => {
          const s = await repo.openSession(
            { orgId: org, userId: user, initiator: user, lastActivityAt: 1, status: 'Inprogress', sessionType: 'chat' },
            userMsg(),
            tx,
          );
          sessionId = s._id;
          throw new Error('boom');
        }, { org });
      } catch {
        /* expected */
      }
      const page = await repo.listSessions(q(org, user));
      expect(page.total).to.equal(fx.transactional ? 0 : 1);
      expect(sessionId).to.match(HEX);
    });
  });

  describe('lists', () => {
    it('orders by the requested key with _id as the tie-break, newest first', async () => {
      const org = fx.newId(), user = fx.newId();
      const t = Date.now();
      const a = await open(org, user, { lastActivityAt: t, title: 'a' });
      const b = await open(org, user, { lastActivityAt: t, title: 'b' });
      const c = await open(org, user, { lastActivityAt: t + 10, title: 'c' });
      const page = await repo.listSessions(q(org, user));
      expect(page.rows.map((r) => r._id)).to.deep.equal([c._id, b._id, a._id]);
      expect(page.total).to.equal(3);
      const asc = await repo.listSessions(q(org, user, { sort: { title: 1, _id: -1 } }));
      expect(asc.rows.map((r) => r.title)).to.deep.equal(['a', 'b', 'c']);
    });

    it('paginates with skip and limit, and the total ignores them', async () => {
      const org = fx.newId(), user = fx.newId();
      for (let i = 0; i < 5; i++) await open(org, user, { lastActivityAt: 100 + i, title: `t${i}` });
      const page = await repo.listSessions(q(org, user, { skip: 1, limit: 2 }));
      expect(page.rows.map((r) => r.title)).to.deep.equal(['t3', 't2']);
      expect(page.total).to.equal(5);
    });

    it('omits __v and keeps the lean key set; internal fields stay out of list rows', async () => {
      const org = fx.newId(), user = fx.newId();
      await open(org, user);
      const [row] = (await repo.listSessions(q(org, user))).rows;
      expect(row).to.not.have.property('__v');
      expect(row).to.not.have.property('nextSeq');
      expect(row).to.not.have.property('sessionType');
      expect(row).to.not.have.property('activeRunId');
      expect(row._id).to.match(HEX);
    });

    it('hides other orgs and soft-deleted rows', async () => {
      const org = fx.newId(), other = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      await open(other, user);
      expect((await repo.listSessions(q(org, user))).total).to.equal(2 - 1);
      await repo.softDelete(s._id, actor(org, user));
      expect((await repo.listSessions(q(org, user))).total).to.equal(0);
    });

    it('lists rows shared with the user only when isShared and the user is a member', async () => {
      const org = fx.newId(), owner = fx.newId(), reader = fx.newId(), stranger = fx.newId();
      const s = await open(org, owner);
      await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: reader, accessLevel: 'read' }] });
      const shared = (u: string) => repo.listSessions(q(org, u, { owned: false, shared: true, omitSharedWith: true }));
      const mine = await shared(reader);
      expect(mine.total).to.equal(1);
      expect(mine.rows[0]).to.not.have.property('sharedWith');
      expect((await shared(stranger)).total).to.equal(0);
      // Listed as owner it still has the member list.
      expect((await repo.listSessions(q(org, owner))).rows[0].sharedWith).to.have.length(1);
      // isShared false hides the member entry.
      await repo.setSharing(CHAT, s._id, { isShared: false, sharedWith: [{ userId: reader, accessLevel: 'read' }] });
      expect((await shared(reader)).total).to.equal(0);
    });

    it('lists agent chats shared with the user only when the shared list asks for them; owned lists stay plain chats', async () => {
      const org = fx.newId(), owner = fx.newId(), reader = fx.newId();
      const plain = await open(org, owner);
      const agentChat = await open(org, owner, { sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      await repo.setSharing(CHAT, plain._id, { isShared: true, sharedWith: [{ userId: reader, accessLevel: 'read' }] });
      await repo.setSharing(agent('ag'), agentChat._id, { isShared: true, sharedWith: [{ userId: reader, accessLevel: 'read' }] });
      const shared = (over: Partial<SessionListQuery> = {}) => repo.listSessions(q(org, reader, { owned: false, shared: true, ...over }));
      expect((await shared()).rows.map((r) => r._id)).to.deep.equal([plain._id]);
      const withAgents = await shared({ sharedIncludesAgentChats: true });
      expect(withAgents.rows.map((r) => r._id).sort()).to.deep.equal([plain._id, agentChat._id].sort());
      expect(withAgents.total).to.equal(2);
      // The owner's own list keeps to plain chats whatever the flag says.
      expect((await repo.listSessions(q(org, owner, { sharedIncludesAgentChats: true }))).rows.map((r) => r._id)).to.deep.equal([plain._id]);
    });

    it('shows project-visible chats to members of the project', async () => {
      const org = fx.newId(), owner = fx.newId(), member = fx.newId(), project = fx.newId();
      const a = await open(org, owner, { projectId: project, projectVisibility: 'project' });
      await open(org, owner, { projectId: project, projectVisibility: 'private' });
      const page = await repo.listSessions(q(org, member, { owned: false, shared: true, accessibleProjectIds: [project] }));
      expect(page.rows.map((r) => r._id)).to.deep.equal([a._id]);
      expect((await repo.listSessions(q(org, member, { owned: false, shared: true }))).total).to.equal(0);
    });

    it('filters by search over titles and content-matched ids, date range, shared flag and project', async () => {
      const org = fx.newId(), user = fx.newId(), project = fx.newId();
      const a = await open(org, user, { title: 'Quarterly plan', projectId: project });
      const b = await open(org, user, { title: 'Other' });
      expect((await repo.listSessions(q(org, user, { search: 'quarter' }))).rows.map((r) => r._id)).to.deep.equal([a._id]);
      const viaContent = await repo.listSessions(q(org, user, { search: 'zzz', contentMatchIds: [b._id] }));
      expect(viaContent.rows.map((r) => r._id)).to.deep.equal([b._id]);
      const future = new Date(Date.now() + 3600_000);
      expect((await repo.listSessions(q(org, user, { startDate: future }))).total).to.equal(0);
      expect((await repo.listSessions(q(org, user, { endDate: future }))).total).to.equal(2);
      expect((await repo.listSessions(q(org, user, { isSharedFilter: true }))).total).to.equal(0);
      expect((await repo.listSessions(q(org, user, { projectFilter: { kind: 'project', id: project } }))).rows[0]._id).to.equal(a._id);
      expect((await repo.listSessions(q(org, user, { projectFilter: { kind: 'unassigned' } }))).rows[0]._id).to.equal(b._id);
    });

    it('splits chats from agent chats and filters archives', async () => {
      const org = fx.newId(), user = fx.newId();
      const chat = await open(org, user);
      const ag = await open(org, user, { sessionType: 'agent', agentKey: 'a1', conversationSource: 'agent_chat' });
      expect((await repo.listSessions(q(org, user))).rows.map((r) => r._id)).to.deep.equal([chat._id]);
      const agentList = await repo.listSessions(q(org, user, { scope: agent('a1'), archived: 'notTrue' }));
      expect(agentList.rows.map((r) => r._id)).to.deep.equal([ag._id]);
      expect((await repo.listSessions(q(org, user, { scope: agent('other'), archived: 'notTrue' }))).total).to.equal(0);
      await repo.setArchived(CHAT, chat._id, { by: user });
      expect((await repo.listSessions(q(org, user))).total).to.equal(0);
      const archived = await repo.listSessions(q(org, user, { archived: 'only' }));
      expect(archived.rows.map((r) => r._id)).to.deep.equal([chat._id]);
      expect(archived.rows[0].archivedBy).to.equal(user);
    });

    it('findVisibleSummary returns the projection the page needs, only for visible rows', async () => {
      const org = fx.newId(), user = fx.newId(), other = fx.newId();
      const s = await open(org, user);
      const sum = await repo.findVisibleSummary(q(org, user, { id: s._id }));
      expect(sum).to.include({ _id: s._id, orgId: org, initiator: user });
      expect(sum).to.not.have.property('userId');
      expect(await repo.findVisibleSummary(q(org, other, { id: s._id }))).to.equal(null);
    });
  });

  describe('write-member checks', () => {
    it('admits the owner only on a plain chat: a member with an old write entry is read-only, and a stranger has no access', async () => {
      const org = fx.newId(), owner = fx.newId(), writer = fx.newId(), reader = fx.newId(), stranger = fx.newId();
      const s = await open(org, owner);
      await repo.setSharing(CHAT, s._id, {
        isShared: true,
        sharedWith: [{ userId: writer, accessLevel: 'write' }, { userId: reader, accessLevel: 'read' }],
      });
      const can = async (u: string) => !!(await repo.findForTurn(CHAT, s._id, actor(org, u)));
      expect(await can(owner)).to.equal(true);
      expect(await can(writer)).to.equal(false);
      expect(await can(reader)).to.equal(false);
      expect(await can(stranger)).to.equal(false);
    });

    it('requires isShared for write membership and honours the scope', async () => {
      const org = fx.newId(), owner = fx.newId(), writer = fx.newId();
      const s = await open(org, owner);
      await repo.setSharing(CHAT, s._id, { isShared: false, sharedWith: [{ userId: writer, accessLevel: 'write' }] });
      expect(await repo.findForTurn(CHAT, s._id, actor(org, writer))).to.equal(null);
      expect(await repo.findForTurn(agent('x'), s._id, actor(org, owner))).to.equal(null);
      expect(await repo.findForTurn(CHAT, s._id, actor(fx.newId(), owner))).to.equal(null);
    });

    it('agent access: member level and malformed ids', async () => {
      const org = fx.newId(), owner = fx.newId(), reader = fx.newId();
      const s = await open(org, owner, { sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      await repo.setSharing(agent('ag'), s._id, { isShared: true, sharedWith: [{ userId: reader, accessLevel: 'read' }] });
      expect(await repo.findAgentWithAccess(s._id, 'ag', actor(org, reader), 'read')).to.not.equal(null);
      expect(await repo.findAgentWithAccess(s._id, 'ag', actor(org, reader), 'write')).to.equal(null);
      expect(await repo.findAgentWithAccess(s._id, 'ag', actor(org, owner), 'write')).to.not.equal(null);
      expect(await repo.findAgentWithAccess(s._id, 'zz', actor(org, owner), 'read')).to.equal(null);
      expect(await repo.findAgentWithAccess('not-an-id', 'ag', actor(org, owner), 'read')).to.equal(null);
    });

    it('find* owner lookups', async () => {
      const org = fx.newId(), owner = fx.newId(), writer = fx.newId();
      const s = await open(org, owner, { title: 'mine' });
      await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: writer, accessLevel: 'write' }] });
      expect(await repo.findOwned(CHAT, s._id, actor(org, owner))).to.not.equal(null);
      expect(await repo.findOwned(CHAT, s._id, actor(org, writer))).to.equal(null);
      expect(await repo.findOwnedByInitiator(CHAT, s._id, actor(org, owner))).to.not.equal(null);
      expect(await repo.findForShare(s._id, actor(org, owner))).to.not.equal(null);
      expect(await repo.findForShare(s._id, actor(org, writer))).to.equal(null);
      expect(await repo.findForUnshare(s._id, actor(org, owner))).to.not.equal(null);
      // A plain-chat lookup never finds an agent chat, and the agent scope never finds a plain chat.
      const ag = await open(org, owner, { sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      expect(await repo.findForShare(ag._id, actor(org, owner))).to.equal(null);
      expect(await repo.findForShare(ag._id, actor(org, owner), agent('ag'))).to.not.equal(null);
      expect(await repo.findForShare(ag._id, actor(org, owner), agent('zz'))).to.equal(null);
      expect(await repo.findForUnshare(ag._id, actor(org, owner), agent('ag'))).to.not.equal(null);
      expect(await repo.findForUnshare(s._id, actor(org, owner), agent('ag'))).to.equal(null);
      expect(await repo.findArchivable(CHAT, s._id, actor(org, owner))).to.not.equal(null);
      // userId must be the owner even for a write member.
      expect(await repo.findArchivable(CHAT, s._id, actor(org, writer))).to.equal(null);
      expect(await repo.findDeletable(s._id, actor(org, owner))).to.not.equal(null);
      expect(await repo.findForFeedback(CHAT, s._id, actor(org, writer))).to.not.equal(null);
      expect(await repo.findForFeedback(CHAT, s._id, actor(org, fx.newId()))).to.equal(null);
    });
  });

  describe('change', () => {
    it('recordRunState sets, clears and appends, and bumps __v with the error array like a document save', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      const done = await repo.recordRunState(s._id, {
        status: 'Failed',
        failReason: 'bad',
        lastActivityAt: 5,
        modelInfo: { modelKey: 'k2', modelName: undefined },
        appendError: { message: 'bad', errorType: 'x', metadata: new Map([['code', 'x']]) },
      });
      expect(done).to.include({ status: 'Failed', failReason: 'bad', lastActivityAt: 5 });
      expect(done!.modelInfo!.modelKey).to.equal('k2');
      expect(done!.conversationErrors).to.have.length(1);
      expect(done!.conversationErrors![0].message).to.equal('bad');
      expect(done!.conversationErrors![0].timestamp).to.be.instanceOf(Date);
      expect(done!.__v).to.equal(1);
      const cleared = await repo.recordRunState(s._id, { status: 'Inprogress', failReason: null });
      expect(cleared).to.not.have.property('failReason');
      expect(cleared!.conversationErrors).to.have.length(1);
      expect(await repo.recordRunState(fx.newId(), { status: 'Complete' })).to.equal(null);
    });

    it('setTitle, setSharing (and clearing the link), setArchived round trip', async () => {
      const org = fx.newId(), user = fx.newId(), m = fx.newId();
      const s = await open(org, user);
      expect((await repo.setTitle(s._id, 'new'))!.title).to.equal('new');
      const shared = await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: m, accessLevel: 'read' }] });
      expect(shared).to.include({ isShared: true });
      expect(shared!.sharedWith![0]).to.include({ userId: m, accessLevel: 'read' });
      const unshared = await repo.setSharing(CHAT, s._id, { isShared: false, sharedWith: [], clearShareLink: true });
      expect(unshared).to.include({ isShared: false });
      expect(unshared!.sharedWith).to.deep.equal([]);
      const arch = await repo.setArchived(CHAT, s._id, { by: user });
      expect(arch).to.include({ isArchived: true, archivedBy: user });
      const back = await repo.setArchived(CHAT, s._id, null);
      expect(back).to.include({ isArchived: false });
      expect(back!.archivedBy).to.equal(null);
      expect(await repo.setArchived(agent('a'), s._id, { by: user })).to.equal(null);
    });

    it('softDelete marks the row, hides it, and reports the citations of its messages', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      const [cit] = await repo.createCitations([{ content: 'c', chunkIndex: 0, citationType: 't', metadata: { orgId: org, mimeType: 'm', recordId: 'r', recordName: 'n', origin: 'o' } }]);
      await repo.appendMessages(s._id, org, [botMsg('a', { citations: [{ citationId: cit._id }] })]);
      const res = await repo.softDelete(s._id, actor(org, user));
      expect(res!.session).to.include({ isDeleted: true, deletedBy: user });
      expect(res!.citationIds).to.deep.equal([cit._id]);
      expect(await repo.findOwned(CHAT, s._id, actor(org, user))).to.equal(null);
      let err: unknown;
      try {
        await repo.softDelete(s._id, actor(org, user));
      } catch (e) {
        err = e;
      }
      expect((err as Error).message).to.equal('Conversation not found');
    });

    it('softDeleteAgent, project link and visibility', async () => {
      const org = fx.newId(), user = fx.newId(), project = fx.newId();
      const s = await open(org, user, { sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      const linked = await repo.setProjectLink(agent('ag'), s._id, { projectId: project, projectVisibility: 'private' });
      expect(linked).to.include({ projectId: project, projectVisibility: 'private' });
      expect((await repo.setProjectVisibility(agent('ag'), s._id, 'project'))!.projectVisibility).to.equal('project');
      const unlinked = await repo.setProjectLink(agent('ag'), s._id, null);
      expect(unlinked).to.not.have.property('projectId');
      const del = await repo.softDeleteAgent(s._id, user);
      expect(del).to.include({ isDeleted: true, deletedBy: user });
      expect(await repo.setProjectLink(CHAT, s._id, null)).to.equal(null);
    });

    it('countByProject and unlinkProject', async () => {
      const org = fx.newId(), user = fx.newId(), p1 = fx.newId(), p2 = fx.newId();
      await open(org, user, { projectId: p1 });
      await open(org, user, { projectId: p1 });
      const gone = await open(org, user, { projectId: p1 });
      await repo.softDelete(gone._id, actor(org, user));
      await open(org, user, { projectId: p2 });
      const counts = await repo.countByProject(org, user, [p1, p2]);
      expect(counts.get(p1)).to.equal(2);
      expect(counts.get(p2)).to.equal(1);
      await repo.unlinkProject(p1);
      expect((await repo.countByProject(org, user, [p1])).get(p1)).to.equal(undefined);
      expect((await repo.listByProject({ orgId: org, projectId: p2, userId: user, skip: 0, limit: 10 })).total).to.equal(1);
    });

    it('countByProject counts the chats the person can open: their own and the ones shared to the project', async () => {
      const org = fx.newId(), owner = fx.newId(), other = fx.newId(), project = fx.newId();
      await open(org, owner, { projectId: project, projectVisibility: 'private' });
      await open(org, other, { projectId: project, projectVisibility: 'private' });
      await open(org, other, { projectId: project, projectVisibility: 'project' });
      expect((await repo.countByProject(org, owner, [project])).get(project)).to.equal(2);
      expect((await repo.countByProject(org, other, [project])).get(project)).to.equal(2);
      expect((await repo.countByProject(org, fx.newId(), [project])).get(project)).to.equal(1);
    });

    it('unlinkFromProject clears the link only while the chat still points at that project', async () => {
      const org = fx.newId(), user = fx.newId(), project = fx.newId(), elsewhere = fx.newId();
      const s = await open(org, user, { projectId: project, projectVisibility: 'project' });
      await repo.unlinkFromProject(org, s._id, elsewhere);
      expect(await repo.findById(s._id)).to.include({ projectId: project });
      await repo.unlinkFromProject(org, s._id, project);
      const after = await repo.findById(s._id);
      expect(after).to.not.have.property('projectId');
      expect(after).to.not.have.property('projectVisibility');
    });
  });

  describe('run lease', () => {
    it('reserveRun lets exactly one of two racing requests win, the other waits then conflicts', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user, { status: 'Complete' });
      const results = await Promise.allSettled([
        repo.reserveRun(CHAT, s._id, actor(org, user), 300),
        repo.reserveRun(CHAT, s._id, actor(org, user), 300),
      ]);
      const won = results.filter((r) => r.status === 'fulfilled' && r.value);
      const lost = results.filter((r) => r.status === 'rejected');
      expect(won).to.have.length(1);
      expect(lost).to.have.length(1);
      expect(((lost[0] as PromiseRejectedResult).reason as Error).message).to.match(/still being generated/);
    });

    it('releaseRun only acts for its own run; touchRun refreshes only the matching run', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user, { status: 'Complete' });
      const r = (await repo.reserveRun(CHAT, s._id, actor(org, user), 10))!;
      expect(r.previousStatus).to.equal('Complete');
      await repo.touchRun(s._id, 'someone-else');
      await repo.releaseRun({ ...r, runId: 'someone-else' });
      expect((await repo.findOwned(CHAT, s._id, actor(org, user)))!.status).to.equal('Inprogress');
      await repo.releaseRun(r);
      expect((await repo.findOwned(CHAT, s._id, actor(org, user)))!.status).to.equal('Complete');
    });

    it('returns null for a session the user cannot write and reports a crashed run as Failed', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user, { lastActivityAt: Date.now() - 11 * 60 * 1000 });
      expect(await repo.reserveRun(CHAT, s._id, actor(org, fx.newId()), 10)).to.equal(null);
      expect((await repo.reserveRun(CHAT, s._id, actor(org, user), 10))!.previousStatus).to.equal('Failed');
    });
  });

  describe('messages', () => {
    it('racing appendMessages allocate disjoint, increasing seq blocks', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      await Promise.all(Array.from({ length: 8 }, (_, i) => repo.appendMessages(s._id, org, [botMsg(`a${i}`), botMsg(`b${i}`)])));
      const rows = await repo.listMessages(s._id, { orgId: org });
      const seqs = rows.map((r) => r.dto.seq);
      expect(seqs).to.have.length(17);
      expect(new Set(seqs).size).to.equal(17);
      expect(seqs).to.deep.equal([...seqs].sort((a, b) => a - b));
      expect(await repo.countMessages(s._id, org)).to.equal(17);
    });

    it('listMessages: skip, limit, order, zero limit, other org', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      await repo.appendMessages(s._id, org, [botMsg('2'), userMsg('3'), botMsg('4')]);
      const content = async (o: object) => (await repo.listMessages(s._id, { orgId: org, ...o })).map((r) => r.dto.content);
      expect(await content({})).to.deep.equal(['hi', '2', '3', '4']);
      expect(await content({ order: 'desc', limit: 2 })).to.deep.equal(['4', '3']);
      expect(await content({ skip: 1, limit: 2 })).to.deep.equal(['2', '3']);
      expect(await content({ limit: 0 })).to.deep.equal([]);
      expect(await repo.listMessages(s._id, { orgId: fx.newId() })).to.deep.equal([]);
    });

    it('is read per session: two sessions never share a read', async () => {
      const org = fx.newId(), user = fx.newId();
      const a = await open(org, user, {}, userMsg('A'));
      const b = await open(org, user, {}, userMsg('B'));
      expect((await repo.listMessages(a._id, { orgId: org })).map((r) => r.dto.content)).to.deep.equal(['A']);
      expect((await repo.listMessages(b._id, { orgId: org })).map((r) => r.dto.content)).to.deep.equal(['B']);
    });

    it('listMessagesOfSessions returns each listed session\'s messages, oldest first, like one listMessages per session', async () => {
      const org = fx.newId(), user = fx.newId();
      const a = await open(org, user, {}, userMsg('A1'));
      const b = await open(org, user, {}, userMsg('B1'));
      const other = await open(fx.newId(), user, {}, userMsg('other org'));
      await repo.appendMessages(a._id, org, [botMsg('A2')]);
      await repo.appendMessages(b._id, org, [botMsg('B2')]);
      const grouped = await repo.listMessagesOfSessions([a._id, b._id, other._id], { orgId: org });
      expect([...grouped.keys()].sort()).to.deep.equal([a._id, b._id].sort());
      for (const s of [a, b]) {
        const single = await repo.listMessages(s._id, { orgId: org });
        expect(grouped.get(s._id)).to.deep.equal(single);
      }
      expect((await repo.listMessagesOfSessions([], { orgId: org })).size).to.equal(0);
    });

    it('populates citations on request and leaves ids otherwise', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      const [cit] = await repo.createCitations([{ content: 'c', chunkIndex: 1, citationType: 't', metadata: { orgId: org, mimeType: 'm', recordId: 'rec', recordName: 'n', origin: 'o' } }]);
      await repo.appendMessages(s._id, org, [botMsg('a', { citations: [{ citationId: cit._id, relevanceScore: 0.5 }] })]);
      const plain = (await repo.listMessages(s._id, { orgId: org })).pop()!.dto;
      expect(plain.citations![0].citationId).to.equal(cit._id);
      const pop = (await repo.listMessages(s._id, { orgId: org, withCitations: true })).pop()!.dto;
      const populated = pop.citations![0].citationId as unknown as { _id: string; content: string; metadata: { recordId: string } };
      expect(populated._id).to.equal(cit._id);
      expect(populated.content).to.equal('c');
      expect(populated).to.not.have.property('__v');
      const refs = await repo.listAttachmentsAndCitedRecords(s._id);
      const cited = refs.flatMap((r) => r.citations ?? []).map((c) => (c.citationId as { metadata?: { recordId?: string } }).metadata?.recordId);
      expect(cited).to.include('rec');
    });

    it('replaceMessage keeps id and seq; deleteMessages and appendFeedback', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      const [a, b] = await repo.appendMessages(s._id, org, [botMsg('old', { feedback: [] }), botMsg('x')]);
      const rep = await repo.replaceMessage(a._id, org, botMsg('new'));
      expect(rep).to.include({ _id: a._id, seq: a.seq, content: 'new' });
      expect(await repo.replaceMessage(fx.newId(), org, botMsg('n'))).to.equal(null);
      const fb = await repo.appendFeedback(a._id, org, { isHelpful: true, timestamp: 1 });
      expect(fb!.feedback).to.have.length(1);
      expect(await repo.findMessage(s._id, org, a._id)).to.include({ _id: a._id });
      expect(await repo.findMessage(s._id, fx.newId(), a._id)).to.equal(null);
      expect(await repo.deleteMessages([b._id], org)).to.equal(1);
      expect(await repo.deleteMessages([], org)).to.equal(0);
    });

    it('deleteMessages given a session deletes only that session’s rows', async () => {
      const org = fx.newId(), user = fx.newId();
      const s1 = await open(org, user);
      const s2 = await open(org, user);
      const [a] = await repo.appendMessages(s1._id, org, [botMsg('a')]);
      const [b] = await repo.appendMessages(s2._id, org, [botMsg('b')]);
      expect(await repo.deleteMessages([a._id, b._id], org, undefined, s1._id)).to.equal(1);
      expect(await repo.findMessage(s2._id, org, b._id)).to.include({ _id: b._id });
      expect(await repo.deleteMessages([b._id], org, undefined, s1._id)).to.equal(0);
    });

    it('updateUserQueryContent rewrites a question’s text, and its files only when given', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      const other = await open(org, user);
      const [q1, answer] = await repo.appendMessages(s._id, org, [
        { ...userMsg('old'), attachments: [{ recordId: 'rec-1', recordName: 'a.pdf' } as never] },
        botMsg('answer'),
      ]);
      const text = await repo.updateUserQueryContent(q1._id, org, s._id, 'new');
      expect(text).to.include({ _id: q1._id, seq: q1.seq, content: 'new', messageType: 'user_query' });
      expect(text!.attachments).to.have.length(1);
      const files = await repo.updateUserQueryContent(q1._id, org, s._id, 'newer', [{ recordId: 'rec-2', recordName: 'b.pdf' } as never]);
      expect(files!.attachments!.map((f) => f.recordId)).to.deep.equal(['rec-2']);
      const cleared = await repo.updateUserQueryContent(q1._id, org, s._id, 'newer', []);
      expect(cleared!.attachments).to.have.length(0);
      // Not a question, another session's id, or another org: nothing matches.
      expect(await repo.updateUserQueryContent(answer._id, org, s._id, 'x')).to.equal(null);
      expect(await repo.updateUserQueryContent(q1._id, org, other._id, 'x')).to.equal(null);
      expect(await repo.updateUserQueryContent(q1._id, fx.newId(), s._id, 'x')).to.equal(null);
      expect((await repo.findMessage(s._id, org, q1._id))!.content).to.equal('newer');
    });

    it('a NUL character in message text is stored as written', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user);
      const [m] = await repo.appendMessages(s._id, org, [botMsg('a\u0000b')]);
      expect(m.content).to.equal(fx.stripsNul ? 'ab' : 'a\u0000b');
    });
  });

  describe('search and archive inputs', () => {
    it('findIdsByContent matches the text of any message of the session', async () => {
      const org = fx.newId(), owner = fx.newId();
      const s = await open(org, owner, {}, userMsg('alpha topic'));
      await repo.appendMessages(s._id, org, [userMsg('beta topic')]);
      const hit = async (text: string) => (await repo.findIdsByContent(org, text)).ids;
      expect(await hit('alpha')).to.deep.equal([s._id]);
      expect(await hit('beta')).to.deep.equal([s._id]);
      expect(await hit('gamma')).to.deep.equal([]);
    });

    it('findIdsByContent reports when the search was cut short by its limit', async () => {
      const org = fx.newId(), owner = fx.newId();
      for (let i = 0; i < 3; i++) await open(org, owner, {}, userMsg(`needle ${i}`));
      const cut = await repo.findIdsByContent(org, 'needle', 2);
      expect(cut.ids).to.have.length(2);
      expect(cut.truncated).to.equal(true);
      for (const limit of [3, 10]) {
        const whole = await repo.findIdsByContent(org, 'needle', limit);
        expect(whole.ids).to.have.length(3);
        expect(whole.truncated).to.equal(false);
      }
    });

    it('listArtifactMarkerContents returns the text of messages up to a seq that carry an artifact marker', async () => {
      const org = fx.newId(), user = fx.newId();
      const s = await open(org, user, {}, userMsg('plain'));
      await repo.appendMessages(s._id, org, [botMsg('see ::artifact[a1] here'), botMsg('and ::artifact[b2]')]);
      expect(await repo.listArtifactMarkerContents(s._id, org, 2)).to.deep.equal(['see ::artifact[a1] here']);
      expect((await repo.listArtifactMarkerContents(s._id, org, 3)).sort()).to.deep.equal(['and ::artifact[b2]', 'see ::artifact[a1] here']);
      expect(await repo.listArtifactMarkerContents(s._id, fx.newId(), 3)).to.deep.equal([]);
    });

    it('searchArchived merges archived chats and agent chats, counting each', async () => {
      const org = fx.newId(), user = fx.newId();
      const c = await open(org, user, { title: 'budget chat' });
      const a = await open(org, user, { title: 'budget agent', sessionType: 'agent', agentKey: 'k1', conversationSource: 'agent_chat' });
      const gone = await open(org, user, { title: 'budget old', sessionType: 'agent', agentKey: 'k2', conversationSource: 'agent_chat' });
      await repo.setArchived(CHAT, c._id, { by: user });
      await repo.setArchived(agent('k1'), a._id, { by: user });
      await repo.setArchived(agent('k2'), gone._id, { by: user });
      const res = await repo.searchArchived({ orgId: org, userId: user, escapedSearch: 'budget', contentMatchIds: [], deletedAgentKeys: ['k2'], skip: 0, limit: 10 });
      expect(res.assistantCount).to.equal(1);
      expect(res.agentCount).to.equal(1);
      expect(res.rows.map((r) => r.source).sort()).to.deep.equal(['agent', 'assistant']);
      expect(res.rows[0]._id).to.match(HEX);
      expect(res.rows[0]).to.not.have.property('sessionType');
    });

    it('listArchivedAgentGroups groups by agent, newest group first, with per-agent totals', async () => {
      const org = fx.newId(), user = fx.newId();
      for (const [k, t] of [['g1', 10], ['g1', 20], ['g2', 30]] as const) {
        const s = await open(org, user, { sessionType: 'agent', agentKey: k, conversationSource: 'agent_chat', lastActivityAt: t });
        await repo.setArchived(agent(k), s._id, { by: user });
      }
      const res = await repo.listArchivedAgentGroups({ orgId: org, userId: user, deletedAgentKeys: null, agentSkip: 0, agentLimit: 10, chatsPerAgent: 1 });
      expect(res.totalAgents).to.equal(2);
      expect(res.groups.map((g) => g.agentKey)).to.have.members(['g1', 'g2']);
      const g1 = res.groups.find((g) => g.agentKey === 'g1')!;
      expect(g1.totalCount).to.equal(2);
      expect(g1.conversations).to.have.length(1);
    });

    it('listAgentSharedWithMe and listVisibleSummaries', async () => {
      const org = fx.newId(), owner = fx.newId(), viewer = fx.newId(), project = fx.newId();
      const a = await open(org, owner, { sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      await repo.setSharing(agent('ag'), a._id, { isShared: true, sharedWith: [{ userId: viewer, accessLevel: 'read' }] });
      const shared = await repo.listAgentSharedWithMe({ orgId: org, userId: viewer, agentKey: 'ag', sort: { _id: -1 }, skip: 0, limit: 10 });
      expect(shared.map((r) => r._id)).to.deep.equal([a._id]);
      expect(shared[0]).to.not.have.property('sharedWith');
      const c = await open(org, owner, { title: 'T', projectId: project, projectVisibility: 'project' });
      const hidden = await open(org, owner, { title: 'H' });
      const titles = await repo.listVisibleSummaries(org, viewer, [c._id, hidden._id], [project]);
      expect(titles.map((t) => t._id)).to.deep.equal([c._id]);
      expect(titles[0]).to.include({ title: 'T', projectId: project });
    });

    it('listVisibleSummaries says which agent and project a chat belongs to', async () => {
      const org = fx.newId(), owner = fx.newId(), project = fx.newId();
      const agentChat = await open(org, owner, { title: 'A', sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      const plain = await open(org, owner, { title: 'P' });
      const rows = await repo.listVisibleSummaries(org, owner, [agentChat._id, plain._id], [project]);
      const byId = new Map(rows.map((r) => [r._id, r]));
      expect(byId.get(agentChat._id)).to.include({ title: 'A', sessionType: 'agent', agentKey: 'ag' });
      expect(byId.get(plain._id)).to.include({ title: 'P' });
      expect(byId.get(plain._id)).to.not.have.property('agentKey');
    });

    it('listVisibleSummaries lists an archived chat only where the archive can open it', async () => {
      const org = fx.newId(), owner = fx.newId(), viewer = fx.newId(), member = fx.newId(), project = fx.newId();
      const share = { isShared: true, sharedWith: [{ userId: viewer, accessLevel: 'read' as const }] };
      const assistant = await open(org, owner, { title: 'assistant' });
      await repo.setSharing(CHAT, assistant._id, share);
      await repo.setArchived(CHAT, assistant._id, { by: owner });
      const agentChat = await open(org, owner, { title: 'agent', sessionType: 'agent', agentKey: 'ag', conversationSource: 'agent_chat' });
      await repo.setSharing(agent('ag'), agentChat._id, share);
      await repo.setArchived(agent('ag'), agentChat._id, { by: owner });
      const inProject = await open(org, owner, { title: 'project', projectId: project, projectVisibility: 'project' });
      await repo.setArchived(CHAT, inProject._id, { by: owner });
      const ids = [assistant._id, agentChat._id, inProject._id];
      const seenBy = async (who: string) => (await repo.listVisibleSummaries(org, who, ids, [project])).map((r) => r.title).sort();

      expect(await seenBy(owner)).to.deep.equal(['agent', 'assistant', 'project']);
      expect(await seenBy(viewer)).to.deep.equal(['assistant']); // not the archived agent chat
      expect(await seenBy(member)).to.deep.equal([]); // project members cannot open an archived chat
      const archived = (await repo.listVisibleSummaries(org, owner, [assistant._id], [project]))[0];
      expect(archived.isArchived).to.equal(true);
    });

    it('getCitations filters by org and drops __v', async () => {
      const org = fx.newId();
      const [c] = await repo.createCitations([{ content: 'x', chunkIndex: 0, citationType: 't', metadata: { orgId: org, mimeType: 'm', recordId: 'r', recordName: 'n', origin: 'o' } }]);
      expect((await repo.getCitations([c._id], org)).map((r) => r._id)).to.deep.equal([c._id]);
      expect(await repo.getCitations([c._id], fx.newId())).to.deep.equal([]);
      expect(await repo.getCitations([], org)).to.deep.equal([]);
      expect((await repo.getCitations([c._id], org))[0]).to.not.have.property('__v');
    });
  });
}
