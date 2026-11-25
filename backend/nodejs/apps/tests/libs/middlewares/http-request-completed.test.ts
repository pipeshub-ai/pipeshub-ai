import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import { AddressInfo } from 'node:net';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import {
  getRequestContext,
  setContextIdentity,
} from '../../../src/libs/context/request-context';
import { onPlatformEvent } from '../../../src/libs/platform-events';
import type { PlatformEvents } from '../../../src/libs/platform-events/events';

type Completed = PlatformEvents['http.request.completed'];

const ORG = '64f0c0ffee0000000000000a';
const PY_UA = 'speakeasy-sdk/python 2.0.1 2.946.0 1.0.0 pipeshub-sdk';

describe('http.request.completed', () => {
  let server: http.Server;
  let base: string;
  let events: Completed[];
  let waiters: (() => void)[];
  let unsubscribe: () => void;
  let seenInHandler: unknown;

  const completed = async (n: number): Promise<Completed[]> => {
    while (events.length < n) await new Promise<void>((resolve) => waiters.push(resolve));
    return events;
  };

  const request = (path: string, headers: Record<string, string> = {}) =>
    new Promise<number>((resolve, reject) => {
      const req = http.request(`${base}${path}`, { agent: false, headers }, (res) => {
        res.resume();
        res.on('end', () => resolve(res.statusCode ?? 0));
      });
      req.on('error', reject);
      req.end();
    });

  beforeEach(async () => {
    events = [];
    waiters = [];
    seenInHandler = undefined;
    unsubscribe = onPlatformEvent('http.request.completed', (event) => {
      events.push(event);
      waiters.splice(0).forEach((wake) => wake());
    });
    const app = express();
    app.use(requestContextMiddleware);
    app.use(accessLog());
    // Stands in for the auth middleware.
    app.use((req, _res, next) => {
      if (req.get('authorization')) {
        setContextIdentity({ orgId: ORG, userId: 'u-1' });
        (req as unknown as { user: unknown }).user = {
          orgId: ORG,
          userId: 'u-1',
          email: 'Ada.Lovelace@Example.COM',
          fullName: 'Ada Lovelace',
        };
      }
      next();
    });
    const router = express.Router();
    router.get('/:kbId', (req, res) => {
      seenInHandler = { context: getRequestContext()?.client, request: req.context?.client };
      res.json({ ok: true });
    });
    router.get('/:kbId/missing', (_req, res) => res.status(404).json({}));
    app.use('/api/v1/kb', router);
    app.get('/health', (_req, res) => res.json({ ok: true }));
    server = app.listen(0);
    await new Promise<void>((resolve) => server.once('listening', resolve));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    unsubscribe();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it('carries the route template, outcome, organisation, address domain and the caller classification', async () => {
    await request('/api/v1/kb/kb-123?q=secret', { authorization: 'Bearer x', 'user-agent': PY_UA });
    const [event] = await completed(1);
    expect(event!.durationSeconds).to.be.a('number').and.to.be.greaterThan(0);
    expect({ ...event, durationSeconds: 0 }).to.deep.equal({
      method: 'GET',
      route: '/api/v1/kb/:kbId',
      status: 200,
      durationSeconds: 0,
      outcome: 'success',
      orgId: ORG,
      emailDomain: 'example.com',
      client: {
        channel: 'sdk',
        sdkLanguage: 'python',
        sdkVersion: '2.0.1',
        generatorVersion: '2.946.0',
      },
    });
  });

  it('holds no address, name, user id, URL or header text', async () => {
    await request('/api/v1/kb/kb-123?q=secret', { authorization: 'Bearer x', 'user-agent': PY_UA });
    const text = JSON.stringify((await completed(1))[0]);
    for (const personal of ['Ada', 'Lovelace', 'u-1', 'kb-123', 'secret', 'Bearer', 'speakeasy']) {
      expect(text).not.to.include(personal);
    }
  });

  it('puts the same classification on the request and on the request context', async () => {
    await request('/api/v1/kb/kb-1', { 'x-pipeshub-client': 'pipeshub-mcp/1.4.2' });
    await completed(1);
    const expected = { channel: 'mcp', mcpServerVersion: '1.4.2' };
    expect(seenInHandler).to.deep.equal({ context: expected, request: expected });
    expect(events[0]!.client).to.deep.equal(expected);
  });

  it('says failure for an error status, and leaves the organisation out when nobody signed in', async () => {
    await request('/api/v1/kb/kb-1/missing');
    const [event] = await completed(1);
    expect(event).to.include({ status: 404, outcome: 'failure', emailDomain: 'unknown' });
    expect(event).not.to.have.property('orgId');
    expect(event!.client).to.deep.equal({ channel: 'api' });
  });

  it('is announced for paths that get no access line', async () => {
    await request('/health');
    expect((await completed(1))[0]).to.include({ route: '/health', status: 200 });
  });
});
