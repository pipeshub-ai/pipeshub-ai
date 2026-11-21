/**
 * P7.3 the user routes over HTTP: the real routers, the auth chain's
 * shape (scopes, first-party writes) and the error middleware.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import sinon from 'sinon';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../src/libs/services/logger.service';
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { MemoryService } from '../../../src/modules/agent-harness/application/memory-service';
import { SharedCitationService } from '../../../src/modules/agent-harness/application/shared-citations';
import { AGENT_HARNESS_MEMORIES } from '../../../src/modules/agent-harness/composition';
import {
  MEMORIES_PREFIX,
  createMemoryRouter,
} from '../../../src/modules/agent-harness/transport/memory-routes';
import { PERSONAL_MEMORY } from '../../../src/modules/agent-harness/domain/memory';
import { memoryFixture } from './support/memory-store-contract';
import { continueAs } from '../../helpers/request-org-context';

interface Caller {
  userId: string;
  orgId: string;
  isOAuth?: boolean;
  oauthScopes?: string[];
}

describe('P7.3 memory routes', () => {
  let server: Server;
  let base: string;
  let caller: Caller;
  let store: InMemoryMemoryStore;
  let records: InMemoryRecordReadAccess;
  let sandbox: sinon.SinonSandbox;

  const call = async (method: string, path: string, body?: unknown) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { 'content-type': 'application/json', authorization: `Bearer ${caller.userId}` },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    return { status: response.status, json: text ? JSON.parse(text) : null };
  };

  beforeEach(async () => {
    caller = { userId: 'alice', orgId: 'org-1' };
    sandbox = sinon.createSandbox();
    for (const level of ['error', 'warn', 'info', 'debug'] as const) {
      sandbox.stub(Logger.prototype, level).callsFake(() => undefined as never);
    }
    store = new InMemoryMemoryStore();
    records = new InMemoryRecordReadAccess();
    const container = new Container();
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { ...caller };
        req.tokenPayload = { ...caller };
        continueAs(req.user, next);
      },
    });
    container
      .bind(AGENT_HARNESS_MEMORIES)
      .toConstantValue(
        new MemoryService({
          store,
          settings: { read: async () => PERSONAL_MEMORY },
          access: new SharedCitationService(records),
          runs: { readClaimSubject: async () => null },
        }),
      );
    const app = express();
    app.use(express.json());
    app.use(MEMORIES_PREFIX, createMemoryRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    await new Promise((resolve) => server.once('listening', resolve));
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(() => {
    sandbox.restore();
    server.close();
  });

  it('user routes: list, edit, review and delete their own memories only', async () => {
    records.grant('Bearer alice', 'r1');
    const mine = memoryFixture({ orgId: 'org-1', ownerId: 'alice', label: { complete: true, sources: [{ kind: 'indexed', recordId: 'r1' }] } });
    const proposed = memoryFixture({ orgId: 'org-1', ownerId: 'alice', status: 'proposed', label: { complete: false, sources: [] } });
    await store.propose(mine);
    await store.propose(proposed);
    const listed = await call('GET', MEMORIES_PREFIX);
    expect(listed.status).to.equal(200);
    expect(listed.json.memories.map((m: { memoryId: string }) => m.memoryId)).to.have.members([mine.memoryId, proposed.memoryId]);
    expect(listed.json.memories[0]).to.not.have.property('runId');
    expect((await call('GET', `${MEMORIES_PREFIX}?status=proposed`)).json.memories).to.have.length(1);
    const edited = await call('PATCH', `${MEMORIES_PREFIX}/${mine.memoryId}`, { content: 'Prefers tables.' });
    expect(edited.json).to.deep.include({ content: 'Prefers tables.', origin: 'user' });
    const reviewed = await call('POST', `${MEMORIES_PREFIX}/${proposed.memoryId}/review`, { decision: 'accepted' });
    expect(reviewed.json.memory.status).to.equal('accepted');
    caller = { userId: 'bob', orgId: 'org-1' };
    const foreign = await call('DELETE', `${MEMORIES_PREFIX}/${mine.memoryId}`);
    expect(foreign.status).to.equal(404);
    expect(foreign.json.error.code).to.equal('HTTP_AGENT_HARNESS_MEMORY_NOT_FOUND');
    caller = { userId: 'alice', orgId: 'org-1', isOAuth: true, oauthScopes: ['user:read', 'user:write'] };
    expect((await call('PATCH', `${MEMORIES_PREFIX}/${mine.memoryId}`, { content: 'x' })).status).to.equal(403);
    caller = { userId: 'alice', orgId: 'org-1' };
    expect((await call('PATCH', `${MEMORIES_PREFIX}/${mine.memoryId}`, { content: '' })).status).to.equal(400);
    expect((await call('DELETE', `${MEMORIES_PREFIX}/${mine.memoryId}`)).status).to.equal(204);
    expect(await store.get('org-1', mine.memoryId)).to.equal(null);
  });
});
