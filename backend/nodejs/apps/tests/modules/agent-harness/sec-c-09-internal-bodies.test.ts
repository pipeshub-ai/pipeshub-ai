/**
 * SEC-C-09: the internal listener reads no body before the route's guard has verified the caller, and each kind of
 * route takes a body only as large as its callers send. SEC-C-16: the public app's global limiter counts a
 * request whose path merely holds an `internal` segment.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import { AddressInfo } from 'node:net';
import { Container } from 'inversify';
import jwt from 'jsonwebtoken';

import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_TOKENS,
  AGENT_HARNESS_RUN_CONTENT,
} from '../../../src/modules/agent-harness/composition';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import { AGENT_HARNESS_INTERNAL_PREFIX } from '../../../src/modules/agent-harness/transport/internal-routes';
import { createAgentHarnessInternalApp } from '../../../src/modules/agent-harness/transport/internal-listener';
import { INTERNAL_JSON_LIMITS } from '../../../src/modules/agent-harness/transport/internal-auth';
import { createGlobalRateLimiter } from '../../../src/libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../src/libs/services/logger.service';

const serve = async (app: express.Express): Promise<{ base: string; close: () => Promise<void> }> => {
  const server = http.createServer((req, res) => {
    app(req, res);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return { base: `http://127.0.0.1:${port}`, close: () => new Promise((resolve) => server.close(() => resolve())) };
};

const P = AGENT_HARNESS_INTERNAL_PREFIX;

interface Seen {
  verified: string[];
  claimed: unknown[];
  refusals: unknown[];
}

const harness = (seen: Seen): Container => {
  const c = new Container();
  c.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
    verifyWorker: async (credential: string | undefined) => {
      seen.verified.push(`worker:${String(credential)}`);
      if (credential !== 'phw_node') throw new HarnessUnauthorizedError('credential', 'worker credential required');
    },
    verifyAttemptToken: async (token: string | undefined, runId: string) => {
      seen.verified.push(`attempt:${String(token)}`);
      if (token !== 'att_ok') throw new HarnessUnauthorizedError('signature', 'attempt token required');
      return { orgId: 'org-1', runId, attemptId: 'a1', fence: 1 };
    },
    workerCredential: () => 'phw_node',
  });
  c.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({
    claimNext: async (request: unknown) => {
      seen.claimed.push(request);
      return null;
    },
  });
  c.bind(AGENT_HARNESS_RUN_CONTENT).toConstantValue({});
  c.bind(AGENT_HARNESS_GATEWAY_TOKENS).toConstantValue({
    recordRefusal: async (token: string | undefined, _runId: string, body: unknown) => {
      if (token !== 'gw_ok') throw new HarnessUnauthorizedError('signature', 'gateway token required');
      seen.refusals.push(body);
      return 'recorded';
    },
  });
  return c;
};

const post = (url: string, body: string, headers: Record<string, string> = {}): Promise<Response> =>
  fetch(url, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body });

describe('SEC-C-09: the internal listener verifies the caller before it reads a body', () => {
  let seen: Seen;
  let base: string;
  let close: () => Promise<void>;
  beforeEach(async () => {
    seen = { verified: [], claimed: [], refusals: [] };
    ({ base, close } = await serve(createAgentHarnessInternalApp(harness(seen))));
  });
  afterEach(async () => close());

  it('an anonymous 9 MB body to a worker route is 401, not parsed', async () => {
    const response = await post(`${base}${P}/runs/claim`, `{"pad":"${'x'.repeat(9 * 1024 * 1024)}"`);
    expect(response.status).to.equal(401);
    expect(seen.verified).to.deep.equal(['worker:undefined']);
  });

  it('a wrong credential with malformed JSON is 401, never a parse error', async () => {
    const response = await post(`${base}${P}/runs/claim`, '{bad', { authorization: 'Bearer phw_wrong' });
    expect(response.status).to.equal(401);
  });

  it('an attempt route refuses an anonymous malformed body as 401', async () => {
    const response = await post(`${base}${P}/runs/r1/commit`, '{bad');
    expect(response.status).to.equal(401);
  });

  it('a run-token route refuses a request with no bearer before reading it', async () => {
    const response = await post(`${base}${P}/runs/r1/refusals`, '{bad');
    expect(response.status).to.equal(401);
    expect(seen.refusals).to.deep.equal([]);
  });

  it('a run-token route takes no more than its small body, even with a bearer', async () => {
    const big = JSON.stringify({ pad: 'x'.repeat(300 * 1024) });
    const response = await post(`${base}${P}/runs/r1/refusals`, big, { authorization: 'Bearer gw_ok' });
    expect(response.status).to.equal(413);
    expect(INTERNAL_JSON_LIMITS['run-token']).to.equal('256kb');
  });

  it('a verified worker route parses its body after the guard, up to its own limit', async () => {
    const ok = await post(
      `${base}${P}/runs/claim`,
      JSON.stringify({ role: 'loop', workerId: 'w1', leaseDurationMs: 30_000 }),
      { authorization: 'Bearer phw_node' },
    );
    expect(ok.status, await ok.text()).to.not.equal(401);
    expect(ok.status).to.not.equal(413);
    const big = await post(`${base}${P}/runs/claim`, JSON.stringify({ pad: 'x'.repeat(2 * 1024 * 1024) }), {
      authorization: 'Bearer phw_node',
    });
    expect(big.status).to.equal(413);
  });

  it('the app itself mounts no body parser in front of the routes', () => {
    const app = createAgentHarnessInternalApp(harness(seen)) as unknown as {
      _router?: { stack: Array<{ name: string }> };
      router?: { stack: Array<{ name: string }> };
    };
    const stack = (app._router ?? app.router)!.stack.map((layer) => layer.name);
    expect(stack).to.not.include('jsonParser');
  });
});

describe('SEC-C-16: the global limiter skips only a verified service call', () => {
  const logger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} } as unknown as Logger;
  const SECRET = 'scoped-secret-for-tests';
  // Signed when a test runs, not when the file loads: a serial run reaches these tests minutes after loading, past
  // the token's minute, and the limiter rightly counts an expired token.
  const scoped = (): string =>
    jwt.sign({ orgId: 'o1', scopes: ['conversation:create'] }, SECRET, { algorithm: 'HS256', expiresIn: 60 });

  const statuses = async (paths: string[], headers: Record<string, string> = {}): Promise<number[]> => {
    const app = express();
    app.use(createGlobalRateLimiter(logger, 2, SECRET));
    app.all(/.*/, (_req, res) => {
      res.status(200).send('ok');
    });
    const { base, close } = await serve(app);
    try {
      const out: number[] = [];
      for (const path of paths) out.push((await fetch(`${base}${path}`, { headers })).status);
      return out;
    } finally {
      await close();
    }
  };

  const four = (path: string): string[] => [path, path, path, path];

  it("an id segment named 'internal' is counted", async () => {
    expect(await statuses(four('/api/v1/agents/internal/conversations/c1'))).to.include(429);
  });

  it('a trailing /internal, an encoded segment and a different case are counted', async () => {
    for (const path of ['/api/v1/conversations/internal', '/api/v1/conversations/%69nternal/x', '/api/v1/conversations/INTERNAL/x']) {
      expect(await statuses(four(path)), path).to.include(429);
    }
  });

  it('an internal route with a forged or expired scoped token is counted', async () => {
    const forged = jwt.sign({ orgId: 'o1', scopes: ['x'] }, 'not-the-secret', { algorithm: 'HS256' });
    const expired = jwt.sign({ orgId: 'o1', scopes: ['x'], exp: Math.floor(Date.now() / 1000) - 60 }, SECRET, { algorithm: 'HS256' });
    for (const token of [forged, expired, 'not-a-jwt']) {
      expect(await statuses(four('/api/v1/conversations/internal/create'), { authorization: `Bearer ${token}` })).to.include(429);
    }
  });

  it('an internal route with a verified scoped token is not counted', async () => {
    const got = await statuses(four('/api/v1/conversations/internal/create'), { authorization: `Bearer ${scoped()}` });
    expect(got).to.deep.equal([200, 200, 200, 200]);
  });

  it('a verified scoped token on a path with no internal segment is still counted', async () => {
    expect(await statuses(four('/api/v1/conversations/x/internalish'), { authorization: `Bearer ${scoped()}` })).to.include(429);
  });
});
