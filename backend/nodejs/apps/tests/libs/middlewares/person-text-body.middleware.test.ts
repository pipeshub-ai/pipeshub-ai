import { expect } from 'chai';
import express from 'express';
import http from 'node:http';
import { AddressInfo } from 'node:net';

import {
  PERSON_TEXT_ROUTES,
  personTextJsonBody,
} from '../../../src/libs/middlewares/person-text-body.middleware';

/** SEC-C-01's event-loop net: the routes that take a person's text parse at 2 MB, the rest at the shared 10 MB. */
describe('SEC-C-01: a smaller JSON bound on the routes that take a person\'s text', () => {
  const statusOf = async (path: string, bytes: number, env: NodeJS.ProcessEnv = { PIPESHUB_HARDENED: 'true' }): Promise<number> => {
    const app = express();
    app.use([...PERSON_TEXT_ROUTES], personTextJsonBody(env));
    app.use(express.json({ limit: '10mb' }));
    app.post(/.*/, (_req, res) => {
      res.status(200).end();
    });
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-parameter handler as an error handler
    app.use((err: { status?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
      res.status(err.status ?? 500).end();
    });
    const server = http.createServer((req, res) => {
      app(req, res);
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    try {
      const response = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ query: 'x'.repeat(bytes) }),
      });
      return response.status;
    } finally {
      await new Promise((resolve) => server.close(() => resolve(undefined)));
    }
  };

  for (const path of ['/api/v1/conversations/s1/runs', '/api/v1/conversations/stream', '/api/v1/agents/a1/conversations/c1/messages/stream', '/api/v1/agents/a1/runs']) {
    it(`${path}: a 3 MB body is 413, a 1 MB one parses`, async () => {
      expect(await statusOf(path, 3 * 1024 * 1024)).to.equal(413);
      expect(await statusOf(path, 1024 * 1024)).to.equal(200);
    });
  }

  it('R51-COMPAT: on the compatibility profile the routes keep the shared 10 MB bound they had (ADR-OPS-07)', async () => {
    expect(await statusOf('/api/v1/conversations/stream', 3 * 1024 * 1024, {})).to.equal(200);
    expect(await statusOf('/api/v1/conversations/stream', 11 * 1024 * 1024, {})).to.equal(413);
  });

  it('an agent definition route keeps the shared bound', async () => {
    expect(await statusOf('/api/v1/agents/a1', 3 * 1024 * 1024)).to.equal(200);
    expect(await statusOf('/api/v1/agents/runsx', 3 * 1024 * 1024)).to.equal(200);
  });
});
