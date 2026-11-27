/** CPD11-04: a cross-origin editor can send the revision it read and see the one it gets back. */
import { expect } from 'chai';
import cors from 'cors';
import express from 'express';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { corsOptions } from '../../../src/libs/middlewares/cors.options';

describe('CPD11-04 CORS for control-plane revisions', () => {
  let server: Server;
  let base: string;
  const origin = 'https://app.example.test';

  before(async () => {
    const app = express();
    app.use(cors(corsOptions({ ALLOWED_ORIGINS: origin })));
    app.put('/x', (_req, res) => {
      res.setHeader('ETag', '"4"');
      res.status(200).json({});
    });
    server = app.listen(0, '127.0.0.1');
    await new Promise((resolve) => server.once('listening', resolve));
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });
  after(() => server.close());

  it('allows If-Match, If-None-Match and Idempotency-Key in a preflight, and exposes ETag', async () => {
    const preflight = await fetch(`${base}/x`, {
      method: 'OPTIONS',
      headers: {
        origin,
        'access-control-request-method': 'PUT',
        'access-control-request-headers': 'if-match,if-none-match,idempotency-key',
      },
    });
    const allowed = (preflight.headers.get('access-control-allow-headers') ?? '').toLowerCase().split(',');
    expect(allowed).to.include.members(['if-match', 'if-none-match', 'idempotency-key']);
    const put = await fetch(`${base}/x`, { method: 'PUT', headers: { origin } });
    expect((put.headers.get('access-control-expose-headers') ?? '').toLowerCase()).to.contain('etag');
  });

  it('keeps the origin list from ALLOWED_ORIGINS', () => {
    expect(corsOptions({ ALLOWED_ORIGINS: 'a,b' }).origin).to.deep.equal(['a', 'b']);
    expect(corsOptions({}).origin).to.deep.equal(['http://localhost:3001']);
  });
});
