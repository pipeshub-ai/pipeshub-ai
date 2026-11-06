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
import { setConversationContext } from '../../../../src/modules/enterprise_search/services/conversation/http/conversation-context';
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

const LIST_FILTER = { $and: [{ orgId: 'org-under-test', $or: [{ userId: 'caller' }] }] };

/** What `guards.listScope('any')` leaves on the request before the handler runs. */
function createMockRequest(
  overrides: Partial<AuthenticatedUserRequest> = {},
): AuthenticatedUserRequest {
  const req = {
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
  setConversationContext(req, {
    caller: { userId: 'caller', orgId: 'org-under-test', teamIds: [] },
    listFilter: LIST_FILTER,
    extended: false,
  });
  return req;
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
      const titlesStub = sinon
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
      expect(titlesStub.firstCall.args).to.deep.equal([
        [
          '507f1f77bcf86cd799439013',
          '507f1f77bcf86cd799439014',
          '507f1f77bcf86cd799439015',
        ],
        {
          readFilter: LIST_FILTER,
          userId: '507f1f77bcf86cd799439011',
          accessibleProjectIds: [],
        },
      ]);
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

const CONVERSATION_ID = '507f1f77bcf86cd799439013';

const CALLER_ID = '507f1f77bcf86cd799439011';
const PROJECT_ID = '507f1f77bcf86cd799439099';

describe('ConversationTitleService', () => {
  afterEach(() => {
    sinon.restore();
  });

  const scope = (accessibleProjectIds: string[] = []) => ({
    readFilter: LIST_FILTER,
    userId: CALLER_ID,
    accessibleProjectIds,
  });

  const stubSessions = (rows: Array<Record<string, unknown>>): sinon.SinonStub =>
    sinon.stub(ChatSession, 'find').returns({
      lean: () => Promise.resolve(rows),
    } as never);

  it('returns an empty map without a query when no valid ids are given', async () => {
    const find = sinon.stub(ChatSession, 'find');
    const titles = await ConversationTitleService.batchTitles(['not-an-id'], scope());
    expect(titles.size).to.equal(0);
    expect(find.called).to.equal(false);
  });

  it('narrows the caller read filter to the ids, never widening it', async () => {
    const find = stubSessions([
      { _id: new Types.ObjectId(CONVERSATION_ID), title: 'Mine' },
    ]);

    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID, CONVERSATION_ID, ''],
      scope(),
    );

    expect(titles.get(CONVERSATION_ID)).to.deep.equal({ title: 'Mine' });
    const [filter] = find.firstCall.args as [{ $and: unknown[] }];
    expect(filter.$and[0]).to.equal(LIST_FILTER);
    expect(filter.$and[1]).to.deep.equal({
      _id: { $in: [new Types.ObjectId(CONVERSATION_ID)] },
    });
  });

  it('lists an archived chat only for its owner or the people an assistant chat is shared with', async () => {
    const find = stubSessions([]);
    await ConversationTitleService.batchTitles([CONVERSATION_ID], scope());
    const [filter] = find.firstCall.args as [{ $and: unknown[] }];
    const caller = new Types.ObjectId(CALLER_ID);
    expect(filter.$and[2]).to.deep.equal({
      $or: [
        { isArchived: { $ne: true } },
        { userId: caller },
        { sessionType: { $ne: 'agent' }, 'sharedWith.userId': caller },
      ],
    });
  });

  it('names the project only when the caller can view it', async () => {
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        title: 'Project chat',
        projectId: new Types.ObjectId(PROJECT_ID),
      },
    ]);
    const visible = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      scope([PROJECT_ID]),
    );
    expect(visible.get(CONVERSATION_ID)).to.deep.equal({
      title: 'Project chat',
      projectId: PROJECT_ID,
    });

    sinon.restore();
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        title: 'Project chat',
        projectId: new Types.ObjectId(PROJECT_ID),
      },
    ]);
    const hidden = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      scope(),
    );
    expect(hidden.get(CONVERSATION_ID)).to.deep.equal({ title: 'Project chat' });
  });

  it('returns the agent of an agent chat and marks an archived one', async () => {
    stubSessions([
      {
        _id: new Types.ObjectId(CONVERSATION_ID),
        title: 'Archived agent chat',
        sessionType: 'agent',
        agentKey: 'agent-7',
        isArchived: true,
      },
    ]);
    const titles = await ConversationTitleService.batchTitles(
      [CONVERSATION_ID],
      scope(),
    );
    expect(titles.get(CONVERSATION_ID)).to.deep.equal({
      title: 'Archived agent chat',
      agentKey: 'agent-7',
      conversationArchived: true,
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
