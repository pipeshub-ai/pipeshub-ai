import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'http';
import { AddressInfo } from 'net';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import { AGENT_HARNESS_INTERNAL_PREFIX } from '../../../src/modules/agent-harness/transport/internal-routes';
import {
  createAgentHarnessInternalApp,
  DEFAULT_INTERNAL_HOST,
  DEFAULT_INTERNAL_PORT,
  DEFAULT_INTERNAL_READ_LIMITS,
  internalListenerConfig,
  internalListenerWanted,
  internalRoutesElsewhere,
} from '../../../src/modules/agent-harness/transport/internal-listener';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { readFileSync } from 'fs';
import { join } from 'path';

/** RS-11 (H6, C-26): the worker-only routes are served on their own listener, and only there. */

const serve = async (
  app: express.Express,
): Promise<{ base: string; close: () => Promise<void> }> => {
  const server = http.createServer((req, res) => {
    app(req, res);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    base: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
};

const container = (): Container => {
  const c = new Container();
  c.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
    verifyWorker: async (credential: string | undefined) => {
      if (credential !== 'phw_node') {
        throw new HarnessUnauthorizedError('credential', 'worker credential required');
      }
    },
    verifyAttemptToken: async () => {
      throw new HarnessUnauthorizedError('signature', 'attempt token required');
    },
  });
  c.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
  return c;
};

describe('RS-11: the agent harness internal listener', () => {
  it('serves the internal router: a route answers 401 without the credential, and 404 off the prefix', async () => {
    const { base, close } = await serve(createAgentHarnessInternalApp(container()));
    try {
      const refused = await fetch(`${base}${AGENT_HARNESS_INTERNAL_PREFIX}/runs/claim`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}',
      });
      expect(refused.status).to.equal(401);
      const events = await fetch(`${base}${AGENT_HARNESS_INTERNAL_PREFIX}/runs/r1/events`, {
        headers: { authorization: 'Bearer not-an-attempt-token' },
      });
      expect(events.status).to.equal(401);
      expect((await fetch(`${base}/api/v1/users`)).status).to.equal(404);
      expect((await fetch(`${base}/api/v1/agent-harness/admin/x`)).status).to.equal(404);
    } finally {
      await close();
    }
  });

  it('the public app answers the internal prefix with 404 and says where the routes are, even with the credential', async () => {
    const app = express();
    app.use(AGENT_HARNESS_INTERNAL_PREFIX, internalRoutesElsewhere());
    app.use(ErrorMiddleware.handleError());
    const { base, close } = await serve(app);
    try {
      const response = await fetch(`${base}${AGENT_HARNESS_INTERNAL_PREFIX}/runs/claim`, {
        method: 'POST',
        headers: { authorization: 'Bearer phw_node', 'content-type': 'application/json' },
        body: '{}',
      });
      expect(response.status).to.equal(404);
      expect(JSON.stringify(await response.json())).to.include('AGENT_HARNESS_INTERNAL_LISTENER');
    } finally {
      await close();
    }
  });

  it('reads its port and host strictly: defaults, overrides, and a malformed port stops startup', () => {
    expect(internalListenerConfig({})).to.deep.equal({
      host: DEFAULT_INTERNAL_HOST,
      port: DEFAULT_INTERNAL_PORT,
      readLimits: DEFAULT_INTERNAL_READ_LIMITS,
    });
    expect(DEFAULT_INTERNAL_PORT).to.equal(3002);
    expect(
      internalListenerConfig({
        AGENT_HARNESS_INTERNAL_PORT: ' 4100 ',
        AGENT_HARNESS_INTERNAL_HOST: '127.0.0.1',
      }),
    ).to.deep.equal({ host: '127.0.0.1', port: 4100, readLimits: DEFAULT_INTERNAL_READ_LIMITS });
    expect(internalListenerConfig({ AGENT_HARNESS_INTERNAL_PORT: '' }).port).to.equal(3002);
    // The native bundle binds every service to loopback; the internal port follows unless told otherwise.
    expect(internalListenerConfig({ PIPESHUB_BIND_HOST: '127.0.0.1' }).host).to.equal('127.0.0.1');
    expect(
      internalListenerConfig({ PIPESHUB_BIND_HOST: '127.0.0.1', AGENT_HARNESS_INTERNAL_HOST: '10.0.0.5' }).host,
    ).to.equal('10.0.0.5');
    for (const bad of ['0', '70000', '30.5', 'abc', '-1']) {
      expect(() => internalListenerConfig({ AGENT_HARNESS_INTERNAL_PORT: bad })).to.throw(
        /AGENT_HARNESS_INTERNAL_PORT/,
      );
    }
  });
});

describe('R51-COMPAT item 6 (R50OFF-FU-5): the internal listener opens only where something reads it', () => {
  it('stays closed with the harness off and code execution not on the broker', () => {
    for (const SANDBOX_MODE of [undefined, '', 'docker', 'e2b', 'local']) {
      expect(internalListenerWanted({ AGENT_HARNESS_WORKER_ENABLED: 'false', SANDBOX_MODE }), String(SANDBOX_MODE)).to.equal(false);
    }
    // Unset is the harness's default since 2026-11-20 (ADR-OPS-06 as amended): on, so the listener opens.
    expect(internalListenerWanted({})).to.equal(true);
  });

  it('opens with the harness on, or for the sandbox broker and egress proxy in broker mode', () => {
    expect(internalListenerWanted({ AGENT_HARNESS_WORKER_ENABLED: 'true', SANDBOX_MODE: 'docker' })).to.equal(true);
    expect(internalListenerWanted({ AGENT_HARNESS_WORKER_ENABLED: 'false', SANDBOX_MODE: ' Broker ' })).to.equal(true);
  });

  it('app.ts creates the internal server only when it is wanted', () => {
    const app = readFileSync(join(__dirname, '../../../src/app.ts'), 'utf8');
    const guard = app.indexOf('if (internalListenerWanted()) {');
    expect(guard).to.be.greaterThan(-1);
    expect(app.indexOf('createAgentHarnessInternalApp(this.esAgentContainer)')).to.be.greaterThan(guard);
  });
});
