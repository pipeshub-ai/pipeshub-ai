/// <reference types="mocha" />
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { AuthorizationService } from '../../../../../src/modules/authz/authz.service';
import { ChatAccessLoader } from '../../../../../src/modules/authz/loaders/chat.loader';
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { Project } from '../../../../../src/modules/projects/schema/project.schema';
import { ConversationGuards } from '../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards';
import {
  ChatNotificationContext,
  loadSessionsForNotifications,
} from '../../../../../src/modules/enterprise_search/services/collaboration/notify/chat-notification-context';
import { AuthenticatedUserRequest } from '../../../../../src/libs/middlewares/types';

const oid = (): Types.ObjectId => new Types.ObjectId();
type Row = Record<string, any>;

describe('chat notification context agrees with the read guard', () => {
  afterEach(() => sinon.restore());

  const ORG = oid();
  const OTHER_ORG = oid();
  const ME = oid();
  const OWNER = oid();
  const TEAM = 'team-1';
  const PROJECT = oid();

  const base = (over: Row): Row => ({
    _id: oid(),
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    sessionType: 'chat',
    sharedWith: [],
    aclVersion: 1,
    isDeleted: false,
    title: 'T',
    ...over,
  });

  const cases: Array<[string, Row, boolean]> = [
    ['owner', base({ userId: ME, initiator: ME }), true],
    [
      'direct read',
      base({ sharedWith: [{ principalType: 'user', userId: ME, accessLevel: 'read' }] }),
      true,
    ],
    [
      'team row',
      base({ sharedWith: [{ principalType: 'team', teamId: TEAM, accessLevel: 'read' }] }),
      true,
    ],
    [
      'project-inherited',
      base({ projectId: PROJECT, projectVisibility: 'project' }),
      true,
    ],
    ['removed', base({ sharedWith: [] }), false],
    ['another org', base({ orgId: OTHER_ORG, userId: ME, initiator: ME }), false],
  ];

  it('context is present exactly when the guard allows read', async () => {
    const sessions = cases.map(([, s]) => s);
    const projects: Row[] = [
      {
        _id: PROJECT,
        orgId: ORG,
        userId: OWNER,
        visibility: 'private',
        members: [{ principalType: 'user', principalId: ME.toString(), role: 'viewer' }],
        aclVersion: 1,
        isDeleted: false,
      },
    ];
    const matches = (s: Row, f: Row): boolean =>
      String(s.orgId) === String(f.orgId) &&
      (f.isDeleted === undefined || s.isDeleted === f.isDeleted);
    sinon.stub(ChatSession, 'findOne').callsFake(((f: Row) => {
      const hit = sessions.find((s) => String(s._id) === String(f._id) && matches(s, f)) ?? null;
      return { select: () => ({ lean: () => Promise.resolve(hit) }) };
    }) as never);
    sinon.stub(ChatSession, 'find').callsFake(((f: Row) => {
      const ids = (f._id.$in as Types.ObjectId[]).map(String);
      const hits = sessions.filter((s) => ids.includes(String(s._id)) && matches(s, f));
      return { select: () => ({ lean: () => ({ exec: () => Promise.resolve(hits) }) }) };
    }) as never);
    const projectOf = (id: unknown): Row | null =>
      projects.find((p) => String(p._id) === String(id)) ?? null;
    sinon.stub(Project, 'findOne').callsFake(((f: Row) => ({
      lean: () => Promise.resolve(projectOf(f._id)),
    })) as never);
    sinon.stub(Project, 'find').callsFake(((f: Row) => ({
      lean: () =>
        Promise.resolve(
          (f._id.$in as unknown[]).map(projectOf).filter((p): p is Row => p !== null),
        ),
    })) as never);

    const flags = { isEnabled: sinon.stub().resolves(true) };
    const chats = new ChatAccessLoader();
    const projectPort = { accessibleProjectIds: sinon.stub().resolves([]), roleOf: sinon.stub(), assertAtLeast: sinon.stub() };
    const teams = {
      callerTeamIds: sinon.stub().resolves({ status: 'ok', teamIds: [TEAM] }),
      teamsVersion: sinon.stub().resolves(0),
      exists: sinon.stub().resolves(true),
    };
    const users = {
      displayNames: sinon.stub().resolves(new Map([[OWNER.toString(), 'Olive']])),
      findByIds: sinon.stub().resolves([{ userId: OWNER.toString(), displayName: 'o', kind: 'human', isDisabled: false }]),
    };
    const authz = new AuthorizationService({ chats, projects: projectPort as never, flags });
    const guards = new ConversationGuards({
      authz,
      chats,
      projects: projectPort as never,
      flags,
      users: users as never,
      teams: teams as never,
      logger: { warn: sinon.stub() },
    });
    const context = new ChatNotificationContext({
      authz,
      teams: teams as never,
      users: users as never,
      flags,
      logger: { warn: sinon.stub() },
      loadSessions: loadSessionsForNotifications,
    });

    const mkReq = (): AuthenticatedUserRequest =>
      ({
        user: { userId: ME.toString(), orgId: ORG.toString() },
        headers: {},
        params: {},
      }) as unknown as AuthenticatedUserRequest;

    const rows = sessions.map((s) => ({
      type: 'chat.shared',
      payload: { sessionId: String(s._id), actorUserId: OWNER.toString() },
    }));
    teams.callerTeamIds.resetHistory();
    const resolved = await context.resolve(mkReq(), rows);
    expect(teams.callerTeamIds.callCount, 'teams resolved at most once per request').to.be.at.most(1);

    for (const [index, [name, s, expected]] of cases.entries()) {
      const allowed = await guards
        .authorizeById(mkReq(), 'read', 'chat', String(s._id))
        .then(() => true, () => false);
      expect(allowed, `guard: ${name}`).to.equal(expected);
      expect(resolved.has(index), `context: ${name}`).to.equal(allowed);
    }
  });
});
