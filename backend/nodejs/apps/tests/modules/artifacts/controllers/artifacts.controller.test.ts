import { expect } from 'chai';
import { NextFunction, Response } from 'express';
import { Types } from 'mongoose';
import sinon from 'sinon';
import {
  deleteArtifact,
  getArtifact,
  listArtifactMembers,
  listArtifactVersions,
  listArtifacts,
  shareArtifact,
  unshareArtifact,
} from '../../../../src/modules/artifacts/controllers/artifacts.controller';
import { ConversationTitleService } from '../../../../src/modules/artifacts/services/conversation-title.service';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ProjectService } from '../../../../src/modules/projects/services/project.service';
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils';
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config';
import {
  ForbiddenError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../../src/libs/errors/http.errors';
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum';
import { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types';

function createMockAppConfig(): AppConfig {
  return {
    connectorBackend: 'http://connector.local',
  } as AppConfig;
}

type JsonResponseDouble = {
  status: sinon.SinonStub;
  json: sinon.SinonStub;
};

function createMockRequest(
  overrides: Partial<AuthenticatedUserRequest> = {},
): AuthenticatedUserRequest {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: {
      userId: '507f1f77bcf86cd799439011',
      orgId: '507f1f77bcf86cd799439012',
    },
    ...overrides,
  } as AuthenticatedUserRequest;
}

function createMockResponse(): JsonResponseDouble {
  const res: JsonResponseDouble = {
    status: sinon.stub(),
    json: sinon.stub(),
  };
  res.status.returns(res);
  res.json.returns(res);
  return res;
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub();
}

async function invoke(
  handler: (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>,
  req: AuthenticatedUserRequest,
  res: JsonResponseDouble,
  next: sinon.SinonStub,
): Promise<void> {
  await handler(
    req,
    res as unknown as Response,
    next as unknown as NextFunction,
  );
}

describe('Artifacts Controller', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('listArtifacts', () => {
    it('proxies to connectors and joins conversation titles', async () => {
      const execStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({
          statusCode: 200,
          data: {
            items: [
              {
                artifactId: 'art-1',
                conversationId: '507f1f77bcf86cd799439013',
              },
              { artifactId: 'art-2' },
              {
                artifactId: 'art-3',
                conversationId: '507f1f77bcf86cd799439014',
              },
              {
                artifactId: 'art-4',
                conversationId: '507f1f77bcf86cd799439015',
              },
            ],
            pagination: { page: 1, limit: 50, totalCount: 4, totalPages: 1 },
          },
        });
      sinon
        .stub(ConversationTitleService, 'batchTitles')
        .resolves(
          new Map([
            ['507f1f77bcf86cd799439013', { title: 'Q3 report' }],
            ['507f1f77bcf86cd799439014', { title: 'Plan', agentKey: 'agent-7' }],
            ['507f1f77bcf86cd799439015', { title: 'Old', projectId: 'proj-1', conversationArchived: true }],
          ]),
        );

      const handler = listArtifacts(createMockAppConfig());
      const req = createMockRequest({
        query: { page: '1', limit: '50', artifactTypes: 'CHART' },
      });
      const res = createMockResponse();
      const next = createMockNext();

      await invoke(handler, req, res, next);

      expect(next.called).to.equal(false);
      expect(execStub.calledOnce).to.equal(true);
      expect(execStub.firstCall.args[0]).to.include(
        '/api/v1/artifacts?page=1&limit=50&artifact_types=CHART',
      );
      expect(res.status.calledWith(200)).to.equal(true);
      const body = res.json.firstCall.args[0];
      expect(body.items[0].conversationTitle).to.equal('Q3 report');
      expect(body.items[0].agentKey).to.equal(undefined);
      expect(body.items[1].conversationTitle).to.equal(undefined);
      expect(body.items[2].conversationTitle).to.equal('Plan');
      expect(body.items[2].agentKey).to.equal('agent-7');
      expect(body.items[2].projectId).to.equal(undefined);
      expect(body.items[3]).to.deep.include({
        conversationTitle: 'Old',
        projectId: 'proj-1',
        conversationArchived: true,
      });
    });

    it('rejects unauthenticated callers', async () => {
      const handler = listArtifacts(createMockAppConfig());
      const req = createMockRequest({ user: undefined });
      const res = createMockResponse();
      const next = createMockNext();

      await invoke(handler, req, res, next);

      expect(next.calledOnce).to.equal(true);
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
    });
  });

  describe('getArtifact', () => {
    it('proxies detail and enriches a visible title', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: {
          artifactId: 'art-1',
          conversationId: '507f1f77bcf86cd799439013',
        },
      });
      sinon
        .stub(ConversationTitleService, 'batchTitles')
        .resolves(new Map([['507f1f77bcf86cd799439013', { title: 'Budget' }]]));

      const handler = getArtifact(createMockAppConfig());
      const req = createMockRequest({ params: { artifactId: 'art-1' } });
      const res = createMockResponse();
      const next = createMockNext();

      await invoke(handler, req, res, next);

      expect(res.json.firstCall.args[0].conversationTitle).to.equal('Budget');
    });

    it('encodes artifactId as a single path segment', async () => {
      const execStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({
          statusCode: 200,
          data: { artifactId: 'x' },
        });

      const handler = getArtifact(createMockAppConfig());
      const req = createMockRequest({
        params: { artifactId: '../knowledgeBase' },
      });
      const res = createMockResponse();
      const next = createMockNext();

      await invoke(handler, req, res, next);

      expect(execStub.firstCall.args[0]).to.equal(
        'http://connector.local/api/v1/artifacts/..%2FknowledgeBase',
      );
    });
  });

  describe('listArtifactVersions', () => {
    it('proxies versions without title lookup', async () => {
      const execStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({
          statusCode: 200,
          data: { versions: [{ version: 1, sizeBytes: 10 }] },
        });
      const titlesStub = sinon.stub(ConversationTitleService, 'batchTitles');

      const handler = listArtifactVersions(createMockAppConfig());
      const req = createMockRequest({ params: { artifactId: 'art-1' } });
      const res = createMockResponse();
      const next = createMockNext();

      await invoke(handler, req, res, next);

      expect(titlesStub.called).to.equal(false);
      expect(execStub.firstCall.args[0]).to.include(
        '/api/v1/artifacts/art-1/versions',
      );
      expect(res.json.firstCall.args[0].versions).to.have.length(1);
    });
  });

  describe('artifact sharing', () => {
    const USER_IDS = ['507f1f77bcf86cd799439021'];

    it('forwards the ownership tab to the gallery query', async () => {
      const execStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({ statusCode: 200, data: { items: [] } });

      const req = createMockRequest({ query: { ownership: 'shared' } });
      await invoke(
        listArtifacts(createMockAppConfig()),
        req,
        createMockResponse(),
        createMockNext(),
      );

      expect(execStub.firstCall.args[0]).to.include('ownership=shared');
    });

    it('lists members without a body', async () => {
      const execStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({ statusCode: 200, data: { members: [] } });

      const res = createMockResponse();
      await invoke(
        listArtifactMembers(createMockAppConfig()),
        createMockRequest({ params: { artifactId: 'art-1' } }),
        res,
        createMockNext(),
      );

      const [uri, method, , body] = execStub.firstCall.args;
      expect(uri).to.equal(
        'http://connector.local/api/v1/artifacts/art-1/permissions',
      );
      expect(method).to.equal(HttpMethod.GET);
      expect(body).to.equal(undefined);
      expect(res.json.firstCall.args[0]).to.deep.equal({ members: [] });
    });

    it('shares and unshares with only the validated user IDs', async () => {
      const execStub = sinon
        .stub(connectorUtils, 'executeConnectorCommand')
        .resolves({ statusCode: 200, data: { shared: 1 } });

      for (const [handler, method] of [
        [shareArtifact, HttpMethod.POST],
        [unshareArtifact, HttpMethod.DELETE],
      ] as const) {
        await invoke(
          handler(createMockAppConfig()),
          createMockRequest({
            params: { artifactId: 'art-1' },
            body: { userIds: USER_IDS, orgId: 'spoofed' },
          }),
          createMockResponse(),
          createMockNext(),
        );
        const [uri, sentMethod, headers, body] = execStub.lastCall.args;
        expect(uri).to.equal(
          'http://connector.local/api/v1/artifacts/art-1/permissions',
        );
        expect(sentMethod).to.equal(method);
        expect(headers.authorization).to.equal('Bearer test-token');
        expect(body).to.deep.equal({ userIds: USER_IDS });
      }
    });

    it('passes a 403 from the connectors service through', async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 403,
        data: { detail: "Only the artifact's owner can manage its sharing" },
      });

      const next = createMockNext();
      await invoke(
        shareArtifact(createMockAppConfig()),
        createMockRequest({
          params: { artifactId: 'art-1' },
          body: { userIds: USER_IDS },
        }),
        createMockResponse(),
        next,
      );

      const error = next.firstCall.args[0];
      expect(error).to.be.instanceOf(ForbiddenError);
      expect(error.message).to.include('owner');
    });

    it('rejects unauthenticated callers before calling the service', async () => {
      const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand');
      const next = createMockNext();

      await invoke(
        shareArtifact(createMockAppConfig()),
        createMockRequest({ user: undefined, params: { artifactId: 'art-1' } }),
        createMockResponse(),
        next,
      );

      expect(execStub.called).to.equal(false);
      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
    });
  });
});

const CALLER_ID = '507f1f77bcf86cd799439011';
const OTHER_USER_ID = '507f1f77bcf86cd799439099';
const ORG_ID = '507f1f77bcf86cd799439012';
const CONVERSATION_ID = '507f1f77bcf86cd799439013';
const PROJECT_ID = '507f1f77bcf86cd799439014';

function sameId(left: unknown, right: unknown): boolean {
  if (left == null || right == null) return false;
  return String(left) === String(right);
}

/** Apply the filter `batchTitles` hands to Mongo against one fixture row. */
function documentMatches(
  doc: Record<string, unknown>,
  clause: Record<string, unknown>,
): boolean {
  return Object.entries(clause).every(([key, expected]) => {
    if (key === '$or' && Array.isArray(expected)) {
      return expected.some(
        (branch) =>
          branch &&
          typeof branch === 'object' &&
          documentMatches(doc, branch as Record<string, unknown>),
      );
    }
    if (key === '$and' && Array.isArray(expected)) {
      return expected.every(
        (branch) =>
          branch &&
          typeof branch === 'object' &&
          documentMatches(doc, branch as Record<string, unknown>),
      );
    }
    return valueMatches(valueAt(doc, key), expected);
  });
}

function valueAt(doc: Record<string, unknown>, path: string): unknown {
  if (path === 'sharedWith.userId') {
    const sharedWith = doc.sharedWith;
    if (!Array.isArray(sharedWith)) return undefined;
    return sharedWith.map((entry) => entry?.userId);
  }
  return doc[path];
}

function valueMatches(actual: unknown, expected: unknown): boolean {
  if (
    expected &&
    typeof expected === 'object' &&
    '$in' in (expected as object)
  ) {
    const allowed = (expected as { $in: unknown[] }).$in;
    const values = Array.isArray(actual) ? actual : [actual];
    return values.some((value) =>
      allowed.some((candidate) => sameId(value, candidate)),
    );
  }
  if (
    expected &&
    typeof expected === 'object' &&
    '$ne' in (expected as object)
  ) {
    const rejected = (expected as { $ne: unknown }).$ne;
    return !(sameId(actual, rejected) || actual === rejected);
  }
  if (Array.isArray(actual)) {
    return actual.some(
      (value) => sameId(value, expected) || value === expected,
    );
  }
  return sameId(actual, expected) || actual === expected;
}

function stubSessions(docs: Record<string, unknown>[]) {
  return sinon.stub(ChatSession, 'find').callsFake(((
    filter: Record<string, unknown>,
  ) => ({
    lean: () =>
      Promise.resolve(docs.filter((doc) => documentMatches(doc, filter))),
  })) as never);
}

describe('ConversationTitleService', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('returns an empty map when no valid ids are given', async () => {
    const titles = await ConversationTitleService.batchTitles(
      ['not-an-id'],
      ORG_ID,
      CALLER_ID,
    );
    expect(titles.size).to.equal(0);
  });

  it('yields no title when isShared is true but sharedWith is a different user', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    const findStub = stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        orgId: new Types.ObjectId(ORG_ID),
        userId: new Types.ObjectId(OTHER_USER_ID),
        isDeleted: false,
        isShared: true,
        sharedWith: [{ userId: new Types.ObjectId(OTHER_USER_ID) }],
        title: 'Shared with someone else',
      },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.size).to.equal(0);
    const filter = findStub.firstCall.args[0] as {
      $or: Array<Record<string, unknown>>;
    };
    expect(filter.$or.some((branch) => branch.isShared === true)).to.equal(
      false,
    );
    expect(filter.$or[1]).to.deep.equal({
      $and: [
        { isShared: true },
        { 'sharedWith.userId': new Types.ObjectId(CALLER_ID) },
        {
          $or: [
            { sessionType: { $ne: 'agent' } },
            { isArchived: { $ne: true } },
          ],
        },
      ],
    });
  });

  it('returns a title when the caller is on sharedWith', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        orgId: new Types.ObjectId(ORG_ID),
        userId: new Types.ObjectId(OTHER_USER_ID),
        isDeleted: false,
        isShared: true,
        sharedWith: [{ userId: new Types.ObjectId(CALLER_ID) }],
        title: 'Shared with me',
      },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({ title: 'Shared with me' });
  });

  it('returns a title for a project-visible chat in a project the caller can view', async () => {
    sinon
      .stub(ProjectService, 'getAccessibleProjectIds')
      .resolves([new Types.ObjectId(PROJECT_ID)]);
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        orgId: new Types.ObjectId(ORG_ID),
        userId: new Types.ObjectId(OTHER_USER_ID),
        projectId: new Types.ObjectId(PROJECT_ID),
        projectVisibility: 'project',
        isDeleted: false,
        isShared: false,
        sharedWith: [],
        title: 'Project chat',
      },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({
      title: 'Project chat',
      projectId: PROJECT_ID,
    });
  });

  it('leaves out the project of an own chat whose project the caller can no longer view', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        orgId: new Types.ObjectId(ORG_ID),
        userId: new Types.ObjectId(CALLER_ID),
        projectId: new Types.ObjectId(PROJECT_ID),
        isDeleted: false,
        title: 'Old project chat',
      },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({ title: 'Old project chat' });
  });

  const archivedFor = (overrides: Record<string, unknown>) => ({
    _id: new Types.ObjectId(CONVERSATION_ID),
    orgId: new Types.ObjectId(ORG_ID),
    userId: new Types.ObjectId(OTHER_USER_ID),
    isDeleted: false,
    isArchived: true,
    title: 'Archived by its owner',
    ...overrides,
  });

  it('keeps an archived assistant chat for a viewer it is shared with', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    stubSessions([
      archivedFor({
        isShared: true,
        sharedWith: [{ userId: new Types.ObjectId(CALLER_ID) }],
      }),
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({
      title: 'Archived by its owner',
      conversationArchived: true,
    });
  });

  it('drops an archived agent chat for a viewer it is shared with', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    stubSessions([
      archivedFor({
        sessionType: 'agent',
        agentKey: 'agent-7',
        isShared: true,
        sharedWith: [{ userId: new Types.ObjectId(CALLER_ID) }],
      }),
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.size).to.equal(0);
  });

  it('drops an archived project chat for other project members', async () => {
    sinon
      .stub(ProjectService, 'getAccessibleProjectIds')
      .resolves([new Types.ObjectId(PROJECT_ID)]);
    stubSessions([
      archivedFor({
        projectId: new Types.ObjectId(PROJECT_ID),
        projectVisibility: 'project',
      }),
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.size).to.equal(0);
  });

  it('marks an archived chat so the gallery opens it from the archive', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        orgId: new Types.ObjectId(ORG_ID),
        userId: new Types.ObjectId(CALLER_ID),
        isDeleted: false,
        isArchived: true,
        sessionType: 'agent',
        agentKey: 'agent-7',
        title: 'Archived agent chat',
      },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({
      title: 'Archived agent chat',
      agentKey: 'agent-7',
      conversationArchived: true,
    });
  });

  it('returns the agent of an agent chat so the gallery can open it there', async () => {
    sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        orgId: new Types.ObjectId(ORG_ID),
        userId: new Types.ObjectId(CALLER_ID),
        isDeleted: false,
        sessionType: 'agent',
        agentKey: 'agent-7',
        title: 'Agent chat',
      },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      ORG_ID,
      CALLER_ID,
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({
      title: 'Agent chat',
      agentKey: 'agent-7',
    });
  });
});

describe('Artifacts Controller deleteArtifact', () => {
  afterEach(() => {
    sinon.restore();
  });

  function createDeleteResponse(): JsonResponseDouble & { end: sinon.SinonStub } {
    const res = { ...createMockResponse(), end: sinon.stub() };
    res.status.returns(res);
    return res;
  }

  it('sends DELETE for the encoded id with the caller headers and answers 204 with no body', async () => {
    const execStub = sinon
      .stub(connectorUtils, 'executeConnectorCommand')
      .resolves({ statusCode: 204, data: undefined });
    const req = createMockRequest({ params: { artifactId: 'art_1-x' } });
    const res = createDeleteResponse();
    const next = createMockNext();

    await invoke(deleteArtifact(createMockAppConfig()), req, res, next);

    expect(execStub.calledOnce).to.equal(true);
    const [uri, method, headers] = execStub.firstCall.args;
    expect(uri).to.equal('http://connector.local/api/v1/artifacts/art_1-x');
    expect(method).to.equal('DELETE');
    expect(headers).to.have.property('authorization', 'Bearer test-token');
    expect(res.status.calledWith(204)).to.equal(true);
    expect(res.end.calledOnce).to.equal(true);
    expect(res.json.called).to.equal(false);
    expect(next.called).to.equal(false);
  });

  for (const [statusCode, errorType] of [
    [403, ForbiddenError],
    [404, NotFoundError],
    [503, ServiceUnavailableError],
  ] as const) {
    it(`passes a ${statusCode} from the connectors service on as ${errorType.name}`, async () => {
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode,
        data: { detail: 'from python' },
      });
      const res = createDeleteResponse();
      const next = createMockNext();

      await invoke(
        deleteArtifact(createMockAppConfig()),
        createMockRequest({ params: { artifactId: 'art-1' } }),
        res,
        next,
      );

      expect(next.calledOnce).to.equal(true);
      expect(next.firstCall.args[0]).to.be.instanceOf(errorType);
      expect(res.status.called).to.equal(false);
    });
  }

  it('refuses an unauthenticated caller without calling the connectors service', async () => {
    const execStub = sinon.stub(connectorUtils, 'executeConnectorCommand');
    const next = createMockNext();

    await invoke(
      deleteArtifact(createMockAppConfig()),
      createMockRequest({ user: undefined, params: { artifactId: 'art-1' } }),
      createDeleteResponse(),
      next,
    );

    expect(execStub.called).to.equal(false);
    expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
  });
});
