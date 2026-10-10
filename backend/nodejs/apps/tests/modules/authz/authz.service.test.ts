import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { AuthorizationService } from '../../../src/modules/authz/authz.service';
import { DecisionCache } from '../../../src/modules/authz/cache/decision-cache';
import { Decision, LoadedChat } from '../../../src/modules/authz/ports';
import { Subject } from '../../../src/modules/authz/domain/types';
import { SampledDecisionLog } from '../../../src/modules/authz/decision-log';

const ORG = 'o1';
const OWNER = 'u-owner';
const ME = 'u-me';
const TEAM = 'team-1';

const subject = (teamIds: Subject['teamIds'] = []): Subject => ({
  userId: ME,
  orgId: ORG,
  teamIds,
});

const chat = (over: Record<string, any> = {}, project: LoadedChat['project'] = null): LoadedChat => ({
  session: { orgId: ORG, userId: OWNER, sharedWith: [], aclVersion: 3, ...over } as any,
  project,
});

const build = (loaded: LoadedChat | null, opts: { collab?: boolean; cache?: DecisionCache<Decision>; content?: any; project?: any; log?: any; teams?: any } = {}) => {
  const chats = { load: sinon.stub().resolves(loaded) };
  const projects = { roleOf: sinon.stub().resolves(opts.project ?? null), assertAtLeast: sinon.stub(), accessibleProjectIds: sinon.stub() };
  const service = new AuthorizationService({
    chats,
    projects,
    flags: { isEnabled: sinon.stub().resolves(opts.collab ?? true) },
    cache: opts.cache,
    content: opts.content,
    log: opts.log,
    teams: opts.teams,
  });
  return { service, chats, projects };
};

describe('AuthorizationService', () => {
  afterEach(() => sinon.restore());

  describe('chat', () => {
    it('allows the owner and reports the owner path and aclVersion', async () => {
      const { service } = build(chat({ userId: ME }));
      const d = await service.check(subject(), 'send', { type: 'chat', id: 'c1' });
      expect(d).to.deep.include({ allow: true, role: 'owner', aclVersion: 3 });
      expect(d.via).to.deep.equal([{ type: 'owner', ref: ME, role: 'owner' }]);
    });

    it('denies a stranger as not found with no paths', async () => {
      const { service } = build(chat());
      const d = await service.check(subject(), 'read', { type: 'chat', id: 'c1' });
      expect(d).to.deep.include({ allow: false, role: 'none', code: 'CONVERSATION_NOT_FOUND' });
    });

    it('denies a missing chat without throwing', async () => {
      const { service } = build(null);
      const d = await service.check(subject(), 'read', { type: 'chat', id: 'nope' });
      expect(d.allow).to.equal(false);
    });

    it('a read collaborator can read but not send (403 read-only)', async () => {
      const { service } = build(chat({ sharedWith: [{ principalType: 'user', userId: ME, accessLevel: 'read' }] }));
      expect((await service.check(subject(), 'read', { type: 'chat', id: 'c' })).allow).to.equal(true);
      const d = await service.check(subject(), 'send', { type: 'chat', id: 'c' });
      expect(d).to.deep.include({ allow: false, code: 'CONVERSATION_READ_ONLY' });
    });

    it('a team write row grants editor through the resolved team', async () => {
      const { service } = build(chat({ sharedWith: [{ principalType: 'team', teamId: TEAM, accessLevel: 'write' }] }));
      const d = await service.check(subject([TEAM]), 'send', { type: 'chat', id: 'c' });
      expect(d).to.deep.include({ allow: true, role: 'editor' });
    });

    it('unresolved teams with only a team grant is a 503 code and is not cached', async () => {
      const cache = new DecisionCache<Decision>();
      const { service } = build(chat({ sharedWith: [{ principalType: 'team', teamId: TEAM, accessLevel: 'write' }] }), { cache });
      const s = subject('unresolved');
      const d = await service.check(s, 'send', { type: 'chat', id: 'c' });
      expect(d).to.deep.include({ allow: false, code: 'TEAM_RESOLUTION_UNAVAILABLE' });
      expect(await cache.get(s, { orgId: ORG, subjectKey: ME, resourceType: 'chat:send:collab', resourceId: 'c', aclVersion: 3 })).to.equal(undefined);
    });

    it('flag off collapses a write row to read', async () => {
      const { service } = build(chat({ sharedWith: [{ principalType: 'user', userId: ME, accessLevel: 'write' }] }), { collab: false });
      const d = await service.check(subject(), 'send', { type: 'chat', id: 'c' });
      expect(d).to.deep.include({ allow: false, role: 'viewer' });
    });

    it('uses the chat the caller already loaded and does not read again', async () => {
      const { service, chats } = build(null);
      const d = await service.check(subject(), 'read', { type: 'chat', id: 'c' }, { loaded: chat({ userId: ME }) });
      expect(d.allow).to.equal(true);
      expect(chats.load.called).to.equal(false);
    });

    it('treats a preloaded soft-deleted chat as not found', async () => {
      const { service } = build(null);
      const d = await service.check(subject(), 'read', { type: 'chat', id: 'c' }, { loaded: chat({ userId: ME, isDeleted: true }) });
      expect(d).to.deep.include({ allow: false, role: 'none' });
    });

    describe('legacyKind with the flag off', () => {
      const direct = { principalType: 'user', userId: ME, accessLevel: 'write' };
      const run = (kind: 'chat' | 'agent', op: string, over: Record<string, any> = {}, project: LoadedChat['project'] = null) =>
        build(chat(over, project), { collab: false }).service.check(subject(), op, { type: 'chat', id: 'c' }, { legacyKind: kind });

      it('a write recipient is read-only and every denial is not-found', async () => {
        expect((await run('chat', 'read', { sharedWith: [direct] })).allow).to.equal(true);
        expect((await run('chat', 'feedback', { sharedWith: [direct] })).allow).to.equal(true);
        expect(await run('chat', 'send', { sharedWith: [direct] })).to.deep.include({ allow: false, code: 'CONVERSATION_NOT_FOUND' });
        expect(await run('chat', 'delete', { sharedWith: [direct] })).to.deep.include({ allow: false, code: 'CONVERSATION_NOT_FOUND' });
      });

      it('an agent recipient cannot open, a project viewer can read but not cancel', async () => {
        expect((await run('agent', 'read', { sharedWith: [direct] })).allow).to.equal(false);
        const project = { orgId: ORG, ownerId: 'x', visibility: 'private', members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] } as any;
        const inProject = { projectId: 'p1', projectVisibility: 'project' };
        expect((await run('agent', 'read', inProject, project)).allow).to.equal(true);
        expect((await run('agent', 'cancel', inProject, project)).allow).to.equal(false);
        expect((await run('chat', 'feedback', inProject, project)).allow).to.equal(false);
      });

      it('ignores team rows entirely', async () => {
        const team = { principalType: 'team', teamId: TEAM, accessLevel: 'write' };
        const { service } = build(chat({ sharedWith: [team] }), { collab: false });
        const d = await service.check(subject([TEAM]), 'read', { type: 'chat', id: 'c' }, { legacyKind: 'chat' });
        expect(d.allow).to.equal(false);
      });

      it('signals unresolved teams only when a project team could grant', async () => {
        const project = { orgId: ORG, ownerId: 'x', visibility: 'private', members: [{ principalType: 'team', principalId: TEAM, role: 'viewer' }] } as any;
        const { service } = build(chat({ projectId: 'p1', projectVisibility: 'project' }, project), { collab: false });
        const d = await service.check(subject('unresolved'), 'read', { type: 'chat', id: 'c' }, { legacyKind: 'chat' });
        expect(d).to.deep.include({ allow: false, code: 'TEAM_RESOLUTION_UNAVAILABLE' });
      });
    });

    it('rejects an unknown action', async () => {
      const { service } = build(chat());
      let err: any;
      try { await service.check(subject(), 'bogus', { type: 'chat', id: 'c' }); } catch (e) { err = e; }
      expect(err?.statusCode).to.equal(400);
    });

    it('inherits project access through the project facts', async () => {
      const project = { orgId: ORG, ownerId: 'x', visibility: 'private', members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] } as any;
      const { service } = build(chat({ projectId: 'p1', projectVisibility: 'project' }, project));
      const d = await service.check(subject(), 'read', { type: 'chat', id: 'c' });
      expect(d.via).to.deep.equal([{ type: 'project', ref: 'p1', role: 'viewer' }]);
    });

    it('PI-13: serves the cached decision within a version and recomputes after an aclVersion bump', async () => {
      const cache = new DecisionCache<Decision>();
      const row = { principalType: 'user', userId: ME, accessLevel: 'read' };
      const { service, chats } = build(chat({ sharedWith: [row] }), { cache });
      const req = {};
      const ref = { type: 'chat', id: 'c' } as const;
      const first = await service.check(subject(), 'read', ref, { request: req });
      // Revoked in storage but the version is unchanged: the cached allow is served.
      chats.load.resolves(chat({ sharedWith: [] }));
      expect(await service.check(subject(), 'read', ref, { request: req })).to.deep.equal(first);
      // The revoke bumps aclVersion: miss, recomputed.
      chats.load.resolves(chat({ sharedWith: [], aclVersion: 4 }));
      const after = await service.check(subject(), 'read', ref, { request: req });
      expect(after.allow).to.equal(false);
      expect(after.aclVersion).to.equal(4);
    });

    it('TM-03: a teams version bump misses the cached decision', async () => {
      const cache = new DecisionCache<Decision>();
      const teamsVersion = sinon.stub().resolves(0);
      const row = { principalType: 'user', userId: ME, accessLevel: 'read' };
      const { service, chats } = build(chat({ sharedWith: [row] }), { cache, teams: { teamsVersion } });
      const req = {};
      const ref = { type: 'chat', id: 'c' } as const;
      await service.check(subject(), 'read', ref, { request: req });
      chats.load.resolves(chat({ sharedWith: [] }));
      expect((await service.check(subject(), 'read', ref, { request: req })).allow).to.equal(true);
      teamsVersion.resolves(1);
      expect((await service.check(subject(), 'read', ref, { request: req })).allow).to.equal(false);
      expect(teamsVersion.alwaysCalledWith(ORG)).to.equal(true);
    });

    it('gate: a project ACL change invalidates a cached H2 decision even though the chat version is unchanged', async () => {
      const cache = new DecisionCache<Decision>();
      const project = (members: any[], aclVersion: number) => ({ orgId: ORG, ownerId: 'x', visibility: 'private', members, aclVersion }) as any;
      const member = { principalType: 'user', principalId: ME, role: 'viewer' };
      const shared = { projectId: 'p1', projectVisibility: 'project' };
      const { service, chats } = build(chat(shared, project([member], 1)), { cache });
      const req = {};
      const ref = { type: 'chat', id: 'c' } as const;
      expect((await service.check(subject(), 'read', ref, { request: req })).allow).to.equal(true);
      // removeMember bumps only the project's aclVersion.
      chats.load.resolves(chat(shared, project([], 2)));
      expect((await service.check(subject(), 'read', ref, { request: req })).allow).to.equal(false);
    });

    it('gate: a soft-deleted chat is not found even when a decision for it is cached', async () => {
      const cache = new DecisionCache<Decision>();
      const { service, chats } = build(chat({ userId: ME }), { cache });
      const req = {};
      const ref = { type: 'chat', id: 'c' } as const;
      expect((await service.check(subject(), 'read', ref, { request: req })).allow).to.equal(true);
      // The agent-conversation delete path does not bump aclVersion.
      chats.load.resolves(chat({ userId: ME, isDeleted: true }));
      expect((await service.check(subject(), 'read', ref, { request: req })).allow).to.equal(false);
      expect((await service.explain(subject(), ref)).via).to.deep.equal([]);
    });

    it('does not cache a check carrying operation context', async () => {
      const cache = new DecisionCache<Decision>();
      const spy = sinon.spy(cache, 'set');
      const { service } = build(chat({ userId: ME }), { cache });
      await service.check(subject(), 'regenerate', { type: 'chat', id: 'c' }, { operation: { isAuthorOfAnsweredQuestion: true } });
      expect(spy.called).to.equal(false);
    });

    it('logs every deny', async () => {
      const sink = { write: sinon.stub() };
      const { service } = build(chat(), { log: new SampledDecisionLog(sink, 0) });
      await service.check(subject(), 'read', { type: 'chat', id: 'c' }, { requestId: 'r1' });
      expect(sink.write.firstCall.args[0]).to.deep.include({ decision: 'deny', resource: 'chat:c', requestId: 'r1', code: 'CONVERSATION_NOT_FOUND' });
    });
  });

  describe('project', () => {
    const found = (role: string, aclVersion = 2) => ({ role, project: { aclVersion } });

    it('allows when the role meets the action and reports the project aclVersion', async () => {
      const { service } = build(null, { project: found('editor') });
      const d = await service.check(subject(), 'editor', { type: 'project', id: 'p' });
      expect(d).to.deep.include({ allow: true, role: 'editor', aclVersion: 2 });
    });

    it('denies below the required role and for no role', async () => {
      expect((await build(null, { project: found('viewer') }).service.check(subject(), 'editor', { type: 'project', id: 'p' })).allow).to.equal(false);
      expect((await build(null).service.check(subject(), 'viewer', { type: 'project', id: 'p' })).allow).to.equal(false);
    });
  });

  describe('content (H4/H5)', () => {
    const reader = { principalType: 'user', userId: ME, accessLevel: 'read' };
    const att = { type: 'chatAttachment', id: 'a', conversationId: 'c' } as const;
    const art = { type: 'chatArtifact', id: 'x', conversationId: 'c' } as const;

    it('without consent a reader is denied, the uploader allowed', async () => {
      const content = { load: sinon.stub().resolves({ creatorId: OWNER }) };
      const { service } = build(chat({ sharedWith: [reader], settings: { ownerContentShared: false } }), { content });
      expect((await service.check(subject(), 'read', att)).allow).to.equal(false);
      content.load.resolves({ creatorId: ME });
      expect((await service.check(subject(), 'read', att)).allow).to.equal(true);
    });

    it('with consent a reader may read an attachment, but never a staging artifact', async () => {
      const content = { load: sinon.stub().resolves({ creatorId: OWNER, kind: 'report' }) };
      const { service } = build(chat({ sharedWith: [reader], settings: { ownerContentShared: true } }), { content });
      expect((await service.check(subject(), 'read', att)).allow).to.equal(true);
      expect((await service.check(subject(), 'read', art)).allow).to.equal(true);
      content.load.resolves({ creatorId: OWNER, kind: 'STAGING' });
      expect((await service.check(subject(), 'read', art)).allow).to.equal(false);
    });

    it('gate: denies attachments and artifacts of a soft-deleted chat, even to a consenting reader', async () => {
      const content = { load: sinon.stub().resolves({ creatorId: OWNER, kind: 'report' }) };
      const { service } = build(chat({ isDeleted: true, sharedWith: [reader], settings: { ownerContentShared: true } }), { content });
      expect((await service.check(subject(), 'read', att)).allow).to.equal(false);
      expect((await service.check(subject(), 'read', art)).allow).to.equal(false);
    });

    it('fails closed when no content loader is bound', async () => {
      const { service } = build(chat({ sharedWith: [reader], settings: { ownerContentShared: true } }));
      expect((await service.check(subject(), 'read', att)).allow).to.equal(false);
    });
  });

  describe('explain', () => {
    it('returns every path and flags unresolved teams', async () => {
      const { service } = build(chat({ sharedWith: [{ principalType: 'user', userId: ME, accessLevel: 'read' }, { principalType: 'team', teamId: TEAM, accessLevel: 'write' }] }));
      const e = await service.explain(subject([TEAM]), { type: 'chat', id: 'c' });
      expect(e.role).to.equal('editor');
      expect(e.via.map((p) => p.type).sort()).to.deep.equal(['direct', 'team']);
      expect((await service.explain(subject('unresolved'), { type: 'chat', id: 'c' })).teamsUnresolved).to.equal(true);
    });

    it('returns none for a missing chat and rejects non-chat resources', async () => {
      const { service } = build(null);
      expect((await service.explain(subject(), { type: 'chat', id: 'c' })).role).to.equal('none');
      let err: any;
      try { await service.explain(subject(), { type: 'project', id: 'p' }); } catch (e) { err = e; }
      expect(err?.statusCode).to.equal(400);
    });
  });
});
