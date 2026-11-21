import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { NotFoundError } from '../../../src/libs/errors/http.errors';
import {
  AGENT_HARNESS_API_PREFIX,
  AGENT_HARNESS_NOT_ENABLED,
  harnessNamespaceWhileOff,
  whenHarnessSwitchedOn,
} from '../../../src/modules/agent-harness/transport/harness-switch';

/** R50-OFF (ADR-OPS-06): with the harness switched off its routes are not served. */
/** One request to a fresh server of ``served``; its status and JSON body. */
async function call(
  served: express.Express,
  method: string,
  path: string,
): Promise<{ status: number; body: any }> {
  // eslint-disable-next-line @typescript-eslint/no-misused-promises -- Express apps are request listeners
  const server = http.createServer(served);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const { port } = server.address() as AddressInfo;
    const res = await fetch(`http://127.0.0.1:${port}${path}`, { method });
    return { status: res.status, body: await res.json() };
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

describe('R50-OFF the harness switch on the routes', () => {
  const app = (env: Record<string, string | undefined>) => {
    const served = express();
    served.use(AGENT_HARNESS_API_PREFIX, harnessNamespaceWhileOff(env));
    const harness = express.Router();
    harness.get('/runs', (_req, res) => {
      res.json({ harness: true });
    });
    served.use('/api/v1/conversations', whenHarnessSwitchedOn(harness, env));
    served.use(AGENT_HARNESS_API_PREFIX, whenHarnessSwitchedOn(harness, env));
    served.get('/api/v1/conversations/:id', (req, res) => {
      res.json({ classic: req.params.id });
    });
    served.use(
      (
        error: unknown,
        _req: express.Request,
        res: express.Response,
        // Express tells an error handler by its four parameters.
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        _next: express.NextFunction,
      ) => {
        const status = error instanceof NotFoundError ? 404 : 500;
        res
          .status(status)
          .json({ error: { message: (error as Error).message } });
      },
    );
    return served;
  };

  for (const [label, env] of [
    ['false', { AGENT_HARNESS_WORKER_ENABLED: 'false' }],
    ['off', { AGENT_HARNESS_WORKER_ENABLED: 'off' }],
  ] as const) {
    it(`${label}: the harness namespace answers a JSON 404 that says why, for every method`, async () => {
      for (const [method, path] of [
        ['GET', '/api/v1/agent-harness/runs'],
        ['POST', '/api/v1/agent-harness/schedules'],
      ]) {
        const res = await call(app(env), method as string, path as string);
        expect(res.status).to.equal(404);
        expect(res.body.error.message).to.equal(AGENT_HARNESS_NOT_ENABLED);
      }
    });

    it(`${label}: a family nested in a classic prefix reaches the classic route`, async () => {
      const res = await call(app(env), 'GET', '/api/v1/conversations/runs');
      expect(res.body).to.deep.equal({ classic: 'runs' });
    });
  }

  for (const [label, env] of [
    ['on', { AGENT_HARNESS_WORKER_ENABLED: 'true' }],
    ['unset (the default since 2026-11-20)', {}],
  ] as const) {
    it(`${label}: the harness serves both`, async () => {
      expect(
        (await call(app(env), 'GET', '/api/v1/agent-harness/runs')).body,
      ).to.deep.equal({ harness: true });
      expect(
        (await call(app(env), 'GET', '/api/v1/conversations/runs')).body,
      ).to.deep.equal({ harness: true });
    });
  }

  it('app.ts guards the namespace before the first harness family and mounts every family behind the switch', () => {
    const source = readFileSync(
      join(__dirname, '../../../src/app.ts'),
      'utf8',
    ).replace(/\s+/g, ' ');
    const guard = source.indexOf(
      'this.app.use(AGENT_HARNESS_API_PREFIX, harnessNamespaceWhileOff())',
    );
    expect(guard).to.be.greaterThan(0);
    for (const family of [
      'MODEL_ACCOUNTS_PREFIX',
      'AGENT_HARNESS_ADMIN_PREFIX',
      'AGENT_HARNESS_RUNS_PREFIX',
      'SCHEDULES_PREFIX',
      'INBOX_PREFIX',
    ]) {
      expect(
        source.search(new RegExp(`this\\.app\\.use\\( ?${family},`)),
        family,
      ).to.be.greaterThan(guard);
    }
    for (const factory of [
      'createChatHarnessSessionRouter',
      'createAgentHarnessSessionRouter',
      'createAgentConfigRouter',
      'createScheduleRouter',
      'createInboxRouter',
      'createAgentHarnessRunsRouter',
    ]) {
      expect(source.replace(/\s+/g, ''), factory).to.include(
        `whenHarnessSwitchedOn(${factory}(this.esAgentContainer))`,
      );
    }
  });
});
