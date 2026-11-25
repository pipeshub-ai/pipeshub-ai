/**
 * P2.13 RT-4: the publication routes at the gateway (the platform's
 * validation and error middleware, the real gateway service) and the adapter
 * that asks the query service, against an HTTP server answering as the query
 * service's routes do.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { createServer, IncomingMessage, Server } from 'node:http';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { QueryPublicationAuthority } from '../../../src/modules/agent-harness/adapters/query-publication-authority';
import { KnowledgePublicationGateway } from '../../../src/modules/agent-harness/application/knowledge-publications';
import { AGENT_HARNESS_PUBLICATIONS } from '../../../src/modules/agent-harness/composition';
import {
  AuthorityAnswer,
  CreatePublicationBody,
  PublicationAuthority,
  PublicationView,
} from '../../../src/modules/agent-harness/ports/knowledge-publications';
import {
  createPublicationRouter,
  PUBLICATIONS_PREFIX,
} from '../../../src/modules/agent-harness/transport/publication-routes';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';

const VIEW: PublicationView = {
  id: 'pub-1',
  containerKind: 'kb',
  containerId: 'kb-hr',
  audience: { userIds: ['u-alice'], teamIds: [], org: false },
  mode: 'answer_only',
  createdBy: 'u-owner',
  createdAt: 1,
  expiresAt: null,
  revokedAt: null,
  revokedBy: null,
};
const BODY = {
  containerKind: 'kb',
  containerId: 'kb-hr',
  audience: { userIds: ['u-alice'] },
  mode: 'answer_only',
};

class RecordingAuthority implements PublicationAuthority {
  readonly calls: Array<{ action: string; authorization: string; args: unknown[] }> = [];
  answer: AuthorityAnswer<PublicationView> = { ok: true, value: VIEW };

  async create(authorization: string, body: CreatePublicationBody) {
    this.calls.push({ action: 'create', authorization, args: [body] });
    return this.answer;
  }

  async list(authorization: string, kind: string, id: string) {
    this.calls.push({ action: 'list', authorization, args: [kind, id] });
    return this.answer.ok ? { ok: true as const, value: [this.answer.value] } : this.answer;
  }

  async revoke(authorization: string, id: string) {
    this.calls.push({ action: 'revoke', authorization, args: [id] });
    return this.answer;
  }
}

const listen = async (server: Server): Promise<string> => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(null)));
  return `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
};

describe('P2.13 knowledge publication routes (RT-4)', () => {
  let server: Server;
  let base: string;
  let enabled: boolean;
  let authority: RecordingAuthority;

  beforeEach(async () => {
    enabled = true;
    authority = new RecordingAuthority();
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: Record<string, unknown>, _res: unknown, next: () => void) => {
        req['user'] = { userId: 'u-owner', orgId: 'org-1' };
        next();
      },
    });
    container.bind(AGENT_HARNESS_PUBLICATIONS).toConstantValue(
      new KnowledgePublicationGateway({ setting: { enabled: async () => enabled }, authority }),
    );
    const app = express();
    app.use(express.json());
    app.use(PUBLICATIONS_PREFIX, createPublicationRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    await new Promise((resolve) => server.once('listening', resolve));
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(() => {
    server.close();
  });

  const call = async (method: string, path: string, body?: unknown, authorization: string | null = 'Bearer owner-token') => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { 'content-type': 'application/json', ...(authorization !== null ? { authorization } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    return { status: response.status, json: text ? JSON.parse(text) : null };
  };

  it('a valid body is forwarded with the caller\'s own Authorization only', async () => {
    const created = await call('POST', PUBLICATIONS_PREFIX, BODY);
    expect(created.status).to.equal(201);
    expect(created.json).to.deep.equal(VIEW);
    expect(authority.calls).to.deep.equal([
      {
        action: 'create',
        authorization: 'Bearer owner-token',
        args: [{ ...BODY, audience: { userIds: ['u-alice'], teamIds: [], org: false } }],
      },
    ]);
    expect((await call('GET', `${PUBLICATIONS_PREFIX}?containerKind=kb&containerId=kb-hr`)).json).to.deep.equal({ publications: [VIEW] });
    expect((await call('DELETE', `${PUBLICATIONS_PREFIX}/pub-1`)).status).to.equal(200);
  });

  it('a body naming whose publication it is, an empty audience or an unknown mode is 400; nothing is forwarded', async () => {
    for (const body of [
      { ...BODY, orgId: 'org-2' },
      { ...BODY, createdBy: 'u-admin' },
      { ...BODY, audience: {} },
      { ...BODY, mode: 'everything' },
      { ...BODY, containerKind: 'drive' },
      { ...BODY, expiresAt: -1 },
    ]) {
      expect((await call('POST', PUBLICATIONS_PREFIX, body)).status, JSON.stringify(body)).to.equal(400);
    }
    expect(authority.calls).to.deep.equal([]);
  });

  it('flag off: every route is 404 publications_disabled and the query service is not asked', async () => {
    enabled = false;
    const responses = [
      await call('POST', PUBLICATIONS_PREFIX, BODY),
      await call('GET', `${PUBLICATIONS_PREFIX}?containerKind=kb&containerId=kb-hr`),
      await call('DELETE', `${PUBLICATIONS_PREFIX}/pub-1`),
    ];
    expect(responses.map((r) => [r.status, r.json.error.code])).to.deep.equal(
      Array(3).fill([404, 'HTTP_AGENT_HARNESS_PUBLICATIONS_DISABLED']),
    );
    expect(authority.calls).to.deep.equal([]);
  });

  it('the query service\'s refusal keeps its status and code', async () => {
    authority.answer = { ok: false, status: 403, code: 'publication_forbidden', message: 'only an owner' };
    const refused = await call('POST', PUBLICATIONS_PREFIX, BODY);
    expect([refused.status, refused.json.error.code]).to.deep.equal([403, 'HTTP_AGENT_HARNESS_PUBLICATION_FORBIDDEN']);
  });

  it('without a bearer the route is 401', async () => {
    expect((await call('POST', PUBLICATIONS_PREFIX, BODY, null)).status).to.equal(401);
  });
});

describe('P2.13 QueryPublicationAuthority (RT-4)', () => {
  let server: Server;
  let base: string;
  const seen: Array<{ method: string; url: string; headers: IncomingMessage['headers']; body: string }> = [];
  let reply: { status: number; body: unknown } = { status: 201, body: VIEW };

  before(async () => {
    server = createServer((req, res) => {
      let body = '';
      req.on('data', (chunk: Buffer) => (body += chunk.toString()));
      req.on('end', () => {
        seen.push({ method: req.method ?? '', url: req.url ?? '', headers: req.headers, body });
        res.writeHead(reply.status, { 'content-type': 'application/json' });
        res.end(JSON.stringify(reply.body));
      });
    });
    base = await listen(server);
    registerUpstreams({ query: [base] });
  });

  after(() => {
    registerUpstreams({});
    server.close();
  });

  beforeEach(() => {
    seen.length = 0;
  });

  it('asks /api/v1/knowledge/publications with the caller\'s Authorization and parses the view', async () => {
    reply = { status: 201, body: VIEW };
    const authority = new QueryPublicationAuthority(() => base);
    const body = { ...BODY, audience: { userIds: ['u-alice'], teamIds: [], org: false } } as CreatePublicationBody;
    expect(await authority.create('Bearer owner-token', body)).to.deep.equal({ ok: true, value: VIEW });
    expect(seen).to.have.length(1);
    expect([seen[0]!.method, seen[0]!.url]).to.deep.equal(['POST', '/api/v1/knowledge/publications']);
    expect(seen[0]!.headers.authorization).to.equal('Bearer owner-token');
    expect(seen[0]!.headers.cookie).to.equal(undefined);
    expect(JSON.parse(seen[0]!.body)).to.deep.equal(body);
    reply = { status: 200, body: { publications: [VIEW] } };
    expect(await authority.list('Bearer owner-token', 'kb', 'kb hr')).to.deep.equal({ ok: true, value: [VIEW] });
    expect(seen[1]!.url).to.equal('/api/v1/knowledge/publications?containerKind=kb&containerId=kb%20hr');
  });

  it('a refusal as FastAPI answers it keeps its code; an unexpected body is not trusted', async () => {
    const authority = new QueryPublicationAuthority(() => base);
    reply = { status: 403, body: { detail: { code: 'publication_forbidden', message: 'only an owner' } } };
    expect(await authority.revoke('Bearer member', 'pub-1')).to.deep.equal({
      ok: false, status: 403, code: 'publication_forbidden', message: 'only an owner',
    });
    reply = { status: 200, body: { ...VIEW, extra: true } };
    const odd = await authority.revoke('Bearer owner', 'pub-1');
    expect(odd.ok).to.equal(false);
  });
});
