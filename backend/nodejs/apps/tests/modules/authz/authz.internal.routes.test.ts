import 'reflect-metadata';
import { readFileSync } from 'fs';
import { join } from 'path';
import { expect } from 'chai';
import sinon from 'sinon';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import { AuthorizationService } from '../../../src/modules/authz/authz.service';
import { ChatContentCheckService } from '../../../src/modules/authz/chat-content-check.service';
import { AttachmentTurnRow, IChatContentLoader, LoadedChat, RunTurn } from '../../../src/modules/authz/ports';
import { createAuthzInternalRouter } from '../../../src/modules/authz/routes/authz.internal.routes';
import { COLLAB_TYPES } from '../../../src/modules/enterprise_search/services/collaboration/collab.types';
import { DirectoryUser } from '../../../src/modules/user_management/services/user-directory.service';
import { invokeRoute } from '../enterprise_search/helpers/route-invoker';
import { Container } from 'inversify';

const ORG = 'a'.repeat(24);
const OTHER_ORG = 'b'.repeat(24);
const A = 'u-owner-a';
const B = 'u-author-b';
const C = 'u-reader-c';
const X = 'chat-x';
const Y = 'chat-y';
const DENY = { allow: false, aclVersion: null };

interface Session {
  orgId?: string;
  userId: string;
  sharedWith?: Array<Record<string, unknown>>;
  projectId?: string | null;
  projectVisibility?: 'private' | 'project';
  aclVersion?: number;
  isDeleted?: boolean;
}

interface Turn extends AttachmentTurnRow {
  recordId: string;
  runId?: string;
  shareToolResults?: boolean;
}

interface World {
  sessions: Map<string, Session>;
  projects: Map<string, LoadedChat['project']>;
  turns: Turn[];
  users: Map<string, DirectoryUser>;
  teamsOf: Map<string, readonly string[] | 'unresolved'>;
  collab: boolean;
}

const user = (userId: string, over: Partial<DirectoryUser> = {}): DirectoryUser => ({
  userId,
  displayName: userId,
  kind: 'human',
  isDisabled: false,
  ...over,
});

const newWorld = (): World => ({
  sessions: new Map(),
  projects: new Map(),
  turns: [],
  users: new Map([A, B, C].map((id) => [id, user(id)])),
  teamsOf: new Map(),
  collab: true,
});

const reader = (userId: string) => ({ principalType: 'user', userId, accessLevel: 'read' });

const orgOf = (w: World, sessionId: string): string | undefined => {
  const s = w.sessions.get(sessionId);
  return s && (s.orgId ?? ORG);
};

/** Same query semantics as ChatContentLoader: org-scoped through the session, chat-restricted when asked, capped at 20. */
const contentLoader = (w: World): IChatContentLoader => ({
  loadAttachmentContext: (orgId, recordId, conversationId) =>
    Promise.resolve(
      w.turns
        .filter(
          (t) =>
            t.recordId === recordId &&
            orgOf(w, t.sessionId) === orgId &&
            (conversationId === undefined || t.sessionId === conversationId),
        )
        .slice(0, 20)
        .map(({ sessionId, authorUserId, filesShared }) => ({
          sessionId,
          ...(authorUserId !== undefined && { authorUserId }),
          ...(filesShared !== undefined && { filesShared }),
        })),
    ),
  loadArtifactContext: (orgId, conversationId, runId): Promise<RunTurn | null> => {
    const turn = w.turns.find(
      (t) => runId !== undefined && t.runId === runId && t.sessionId === conversationId && orgOf(w, t.sessionId) === orgId,
    );
    return Promise.resolve(
      turn ? { authorUserId: turn.authorUserId, shareToolResults: turn.shareToolResults } : null,
    );
  },
});

const build = (w: World, content: IChatContentLoader = contentLoader(w)) => {
  const chats = {
    load: (orgId: string, chatId: string): Promise<LoadedChat | null> => {
      const s = w.sessions.get(chatId);
      if (!s || (s.orgId ?? ORG) !== orgId) return Promise.resolve(null);
      const project = s.projectId ? (w.projects.get(s.projectId) ?? null) : null;
      return Promise.resolve({
        session: { orgId: s.orgId ?? ORG, sharedWith: [], ...s } as unknown as LoadedChat['session'],
        project,
      });
    },
  };
  const flags = { isEnabled: () => Promise.resolve(w.collab) };
  const authz = new AuthorizationService({
    chats,
    projects: { roleOf: sinon.stub().resolves(null), assertAtLeast: sinon.stub(), accessibleProjectIds: sinon.stub() },
    flags,
  });
  const service = new ChatContentCheckService({
    authz,
    chats,
    content,
    users: {
      displayNames: sinon.stub(),
      findByIds: (_org: string, ids: readonly string[]) =>
        Promise.resolve(ids.flatMap((id) => (w.users.has(id) ? [w.users.get(id)!] : []))),
    },
    teams: { forUser: (userId: string) => Promise.resolve(w.teamsOf.get(userId) ?? []) },
    flags,
    logger: { debug: sinon.stub() } as never,
  });
  const tokens = new AuthTokenService('jwt-secret', 'scoped-secret');
  const container = new Container();
  container.bind('AuthMiddleware').toConstantValue(new AuthMiddleware({ debug: () => undefined, error: () => undefined, warn: () => undefined } as never, tokens));
  container.bind(COLLAB_TYPES.ChatContentCheckService).toConstantValue(service);
  const router = createAuthzInternalRouter(container);
  const bearer = (scopes: string[], orgId = ORG): Record<string, string> => ({
    authorization: `Bearer ${tokens.generateScopedToken({ userId: 'svc', orgId, scopes }, '1m')}`,
  });
  const check = (body: unknown, headers: Record<string, string> = bearer([TokenScopes.AUTHZ_CHECK])) =>
    invokeRoute(router, { method: 'POST', url: '/internal/check', body, headers });
  return { check, bearer, tokens, router, service };
};

const attachmentBody = (userId: string, recordId: string, ownerUserId = B, conversationId?: string) => ({
  userId,
  orgId: ORG,
  action: 'read' as const,
  resource: { type: 'chatAttachment' as const, recordId, ownerUserId, ...(conversationId && { conversationId }) },
});

const artifactBody = (userId: string, over: Record<string, unknown> = {}) => ({
  userId,
  orgId: ORG,
  action: 'read',
  resource: { type: 'chatArtifact', recordId: 'art-1', ownerUserId: B, conversationId: X, runId: 'run-1', ...over },
});

/** Chat X owned by A, C a reader, with B's turn attaching `rec-1`. */
const sharedChat = (w: World, turn: Partial<Turn> = {}, session: Partial<Session> = {}) => {
  w.sessions.set(X, { userId: A, sharedWith: [reader(C)], aclVersion: 4, ...session });
  w.turns.push({ sessionId: X, recordId: 'rec-1', authorUserId: B, filesShared: true, runId: 'run-1', shareToolResults: true, ...turn });
};

describe('POST /internal/check', () => {
  afterEach(() => sinon.restore());

  describe('token (PI-15)', () => {
    it('rejects a missing token, a user JWT and a scoped token with another scope with 401', async () => {
      const w = newWorld();
      sharedChat(w);
      const { check, tokens } = build(w);
      const body = attachmentBody(C, 'rec-1');
      expect((await check(body, {})).status).to.equal(401);
      const userJwt = { authorization: `Bearer ${tokens.generateToken({ userId: C, orgId: ORG })}` };
      expect((await check(body, userJwt)).status).to.equal(401);
      const otherScope = await check(body, build(w).bearer([TokenScopes.STORAGE_TOKEN]));
      expect(otherScope.status).to.equal(401);
    });

    it('rejects a token for another org with 403 before deciding anything', async () => {
      const w = newWorld();
      sharedChat(w);
      const { check, bearer } = build(w);
      const out = await check(attachmentBody(C, 'rec-1'), bearer([TokenScopes.AUTHZ_CHECK], OTHER_ORG));
      expect(out.status).to.equal(403);
    });

    it('accepts the authz:check scope', async () => {
      const w = newWorld();
      sharedChat(w);
      const out = await build(w).check(attachmentBody(C, 'rec-1'));
      expect(out.status).to.equal(200);
    });

    it('rejects a malformed body with 400', async () => {
      const out = await build(newWorld()).check({ userId: C, orgId: ORG, action: 'write', resource: {} });
      expect(out.status).to.equal(400);
    });
  });

  describe('attachments (H4)', () => {
    it('PI-09: without the author consent neither a reader nor the chat owner can read', async () => {
      const w = newWorld();
      sharedChat(w, { filesShared: false });
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal(DENY);
      expect((await check(attachmentBody(A, 'rec-1'))).body).to.deep.equal(DENY);
    });

    it('PI-10: with consent a reader is allowed with the chat aclVersion; removed, the next call denies', async () => {
      const w = newWorld();
      sharedChat(w);
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal({ allow: true, aclVersion: 4 });
      w.sessions.set(X, { ...w.sessions.get(X)!, sharedWith: [], aclVersion: 5 });
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal(DENY);
    });

    it('PH07-04: a record attached in chats X and Y is readable through Y when no conversationId is given', async () => {
      const w = newWorld();
      sharedChat(w);
      w.sessions.set(X, { userId: A, sharedWith: [], aclVersion: 1 });
      w.sessions.set(Y, { userId: A, sharedWith: [reader(C)], aclVersion: 9 });
      w.turns.push({ sessionId: Y, recordId: 'rec-1', authorUserId: B, filesShared: true });
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal({ allow: true, aclVersion: 9 });
    });

    it('a grant on chat Y never authorizes the record through chat X', async () => {
      const w = newWorld();
      w.sessions.set(X, { userId: A, sharedWith: [], aclVersion: 1 });
      w.sessions.set(Y, { userId: A, sharedWith: [reader(C)], aclVersion: 9 });
      w.turns.push({ sessionId: X, recordId: 'rec-x', authorUserId: B, filesShared: true });
      w.turns.push({ sessionId: Y, recordId: 'rec-y', authorUserId: B, filesShared: true });
      const { check } = build(w);
      // rec-x appears only in X, where C has no role; asking through Y finds nothing.
      expect((await check(attachmentBody(C, 'rec-x', B, Y))).body).to.deep.equal(DENY);
      expect((await check(attachmentBody(C, 'rec-x', B, X))).body).to.deep.equal(DENY);
      expect((await check(attachmentBody(C, 'rec-x'))).body).to.deep.equal(DENY);
      expect((await check(attachmentBody(C, 'rec-y', B, Y))).body).to.deep.equal({ allow: true, aclVersion: 9 });
    });

    it('holds even if the loader returns rows from another chat', async () => {
      const w = newWorld();
      w.sessions.set(X, { userId: A, sharedWith: [], aclVersion: 1 });
      w.sessions.set(Y, { userId: A, sharedWith: [reader(C)], aclVersion: 9 });
      w.turns.push({ sessionId: Y, recordId: 'rec-y', authorUserId: B, filesShared: true });
      const loose = { ...contentLoader(w), loadAttachmentContext: () => contentLoader(w).loadAttachmentContext(ORG, 'rec-y') };
      const { service } = build(w, loose);
      expect(await service.check(attachmentBody(C, 'rec-y', B, X))).to.deep.equal(DENY);
      expect(await service.check(attachmentBody(C, 'rec-y', B, Y))).to.deep.equal({ allow: true, aclVersion: 9 });
    });

    it('PH07-22: a turn that lists a record owned by someone else never consents it', async () => {
      const w = newWorld();
      sharedChat(w);
      expect((await build(w).check(attachmentBody(C, 'rec-1', A))).body).to.deep.equal(DENY);
    });

    it('a legacy turn (no author, no consent) denies with the flag on', async () => {
      const w = newWorld();
      sharedChat(w, { authorUserId: undefined, filesShared: undefined });
      expect((await build(w).check(attachmentBody(C, 'rec-1', A))).body).to.deep.equal(DENY);
    });

    it('flag off: a recipient reads the session owner attachment, by legacy parity', async () => {
      const w = newWorld();
      w.collab = false;
      sharedChat(w, { authorUserId: undefined, filesShared: undefined });
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1', A))).body).to.deep.equal({ allow: true, aclVersion: 4 });
      expect((await check(attachmentBody('u-stranger', 'rec-1', A))).body).to.deep.equal(DENY);
    });

    it('PR-7.3 flag-off parity: the exact request Python sends for a recipient without a READER edge is allowed', async () => {
      // Same JSON the Python test (test_record_access.py, TestFlagOffRecipientParity) asserts it sends.
      const wire = JSON.parse(
        readFileSync(join(__dirname, 'fixtures', 'flag-off-attachment-check.json'), 'utf8'),
      ) as ReturnType<typeof attachmentBody>;
      expect(wire).to.deep.equal(attachmentBody(C, 'rec-1', A, X));
      const w = newWorld();
      w.collab = false;
      sharedChat(w, { authorUserId: undefined, filesShared: undefined });
      const { check } = build(w);
      expect((await check(wire)).body).to.deep.equal({ allow: true, aclVersion: 4 });
      expect((await check({ ...wire, userId: 'u-stranger' })).body).to.deep.equal(DENY);
    });

    it('PI-01/PI-08: a project viewer reads a project-visible chat file with consent; deleting the project denies', async () => {
      const w = newWorld();
      w.projects.set('p1', { orgId: ORG, ownerId: A, visibility: 'private', members: [{ principalType: 'user', principalId: C, role: 'viewer' }], aclVersion: 2 });
      sharedChat(w, {}, { sharedWith: [], projectId: 'p1', projectVisibility: 'project' });
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal({ allow: true, aclVersion: 4 });
      w.projects.delete('p1');
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal(DENY);
    });

    it('PI-06/PI-07: flipping the chat private denies the project viewer on the next check', async () => {
      const w = newWorld();
      w.projects.set('p1', { orgId: ORG, ownerId: A, visibility: 'private', members: [{ principalType: 'user', principalId: C, role: 'viewer' }] });
      sharedChat(w, {}, { sharedWith: [], projectId: 'p1', projectVisibility: 'project' });
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.include({ allow: true });
      w.sessions.set(X, { ...w.sessions.get(X)!, projectVisibility: 'private' });
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal(DENY);
    });

    it('a team collaborator reads once the team is resolved for the subject', async () => {
      const w = newWorld();
      sharedChat(w, {}, { sharedWith: [{ principalType: 'team', teamId: 'team-1', accessLevel: 'read' }] });
      w.teamsOf.set(C, ['team-1']);
      const { check } = build(w);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal({ allow: true, aclVersion: 4 });
      w.teamsOf.set(C, []);
      expect((await check(attachmentBody(C, 'rec-1'))).body).to.deep.equal(DENY);
    });

    it('denies when the subject teams cannot be resolved (fail closed)', async () => {
      const w = newWorld();
      sharedChat(w, {}, { sharedWith: [{ principalType: 'team', teamId: 'team-1', accessLevel: 'read' }] });
      w.teamsOf.set(C, 'unresolved');
      expect((await build(w).check(attachmentBody(C, 'rec-1'))).body).to.deep.equal(DENY);
    });

    it('a project team inherits into the chat role', async () => {
      const w = newWorld();
      w.projects.set('p1', { orgId: ORG, ownerId: A, visibility: 'private', members: [{ principalType: 'team', principalId: 'team-9', role: 'viewer' }] });
      sharedChat(w, {}, { sharedWith: [], projectId: 'p1', projectVisibility: 'project' });
      w.teamsOf.set(C, ['team-9']);
      expect((await build(w).check(attachmentBody(C, 'rec-1'))).body).to.deep.include({ allow: true });
    });
  });

  describe('artifacts (H5)', () => {
    it('PH07-02: with the run turn consent a reader is allowed; a legacy artifact without runId is denied', async () => {
      const w = newWorld();
      sharedChat(w);
      const { check } = build(w);
      expect((await check(artifactBody(C))).body).to.deep.equal({ allow: true, aclVersion: 4 });
      expect((await check(artifactBody(C, { runId: undefined }))).body).to.deep.equal(DENY);
      expect((await check(artifactBody(C, { runId: 'other-run' }))).body).to.deep.equal(DENY);
    });

    it('denies when the run turn did not share tool results', async () => {
      const w = newWorld();
      sharedChat(w, { shareToolResults: false });
      expect((await build(w).check(artifactBody(C))).body).to.deep.equal(DENY);
    });

    it('PI-11: a STAGING or temporary artifact is never shared', async () => {
      const w = newWorld();
      sharedChat(w);
      const { check } = build(w);
      expect((await check(artifactBody(C, { kind: { visibility: 'STAGING', isTemporary: false } }))).body).to.deep.equal(DENY);
      expect((await check(artifactBody(C, { kind: { visibility: 'VISIBLE', isTemporary: true } }))).body).to.deep.equal(DENY);
      expect((await check(artifactBody(C, { kind: { visibility: 'VISIBLE', isTemporary: false } }))).body).to.deep.include({ allow: true });
    });

    it('needs a conversationId', async () => {
      const w = newWorld();
      sharedChat(w);
      expect((await build(w).check(artifactBody(C, { conversationId: undefined }))).body).to.deep.equal(DENY);
    });

    it('flag off: any chat reader gets a visible artifact without a run turn', async () => {
      const w = newWorld();
      w.collab = false;
      sharedChat(w);
      const { check } = build(w);
      expect((await check(artifactBody(C, { runId: undefined }))).body).to.deep.equal({ allow: true, aclVersion: 4 });
      expect((await check(artifactBody(C, { runId: undefined, kind: { visibility: 'STAGING' } }))).body).to.deep.equal(DENY);
    });
  });

  describe('no existence oracle (PH07-03)', () => {
    it('answers an identical 200 deny for unknown record, unknown chat, other-org chat, deleted chat and bad subject', async () => {
      const w = newWorld();
      sharedChat(w);
      w.sessions.set('chat-foreign', { orgId: OTHER_ORG, userId: A, sharedWith: [reader(C)] });
      w.turns.push({ sessionId: 'chat-foreign', recordId: 'rec-f', authorUserId: B, filesShared: true });
      w.sessions.set('chat-deleted', { userId: A, sharedWith: [reader(C)], isDeleted: true });
      w.turns.push({ sessionId: 'chat-deleted', recordId: 'rec-d', authorUserId: B, filesShared: true });
      w.users.set('u-svc', user('u-svc', { kind: 'service' }));
      w.users.set('u-off', user('u-off', { isDisabled: true }));
      const { check } = build(w);
      const outcomes = await Promise.all([
        check(attachmentBody(C, 'rec-missing')),
        check(attachmentBody(C, 'rec-1', B, 'chat-missing')),
        check(attachmentBody(C, 'rec-f')),
        check(attachmentBody(C, 'rec-d')),
        check(attachmentBody('u-nobody', 'rec-1')),
        check(attachmentBody('u-svc', 'rec-1')),
        check(attachmentBody('u-off', 'rec-1')),
        check(artifactBody(C, { conversationId: 'chat-missing' })),
        check(artifactBody(C, { conversationId: 'chat-deleted' })),
      ]);
      for (const out of outcomes) {
        expect(out.status).to.equal(200);
        expect(out.body).to.deep.equal(DENY);
      }
    });
  });
});
