import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { Container } from 'inversify';
import {
  SESSION_AUTH_FAILURES_PER_MINUTE,
  sessionAuthFailureRateLimit,
} from '../../../src/modules/agent-harness/transport/session-routes';

describe('session routes: failed authentications from one address are slowed, nothing else is counted', () => {
  async function statuses(path: string, count: number): Promise<number[]> {
    const app = express();
    app.use(sessionAuthFailureRateLimit(new Container(), 'chat'));
    app.get('/unauthenticated', (_req, res) => {
      res.status(401).end();
    });
    app.get('/not-found', (_req, res) => {
      res.status(404).end();
    });
    const server = app.listen(0, '127.0.0.1');
    await new Promise((resolve) => server.once('listening', resolve));
    try {
      const { port } = server.address() as AddressInfo;
      const seen: number[] = [];
      for (let i = 0; i < count; i += 1) {
        seen.push((await fetch(`http://127.0.0.1:${port}${path}`)).status);
      }
      return seen;
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  }

  it('answers 429 once an address has failed to authenticate the allowed number of times', async () => {
    const seen = await statuses('/unauthenticated', SESSION_AUTH_FAILURES_PER_MINUTE + 2);
    expect(seen.slice(0, SESSION_AUTH_FAILURES_PER_MINUTE).every((s) => s === 401)).to.equal(true);
    expect(seen.slice(SESSION_AUTH_FAILURES_PER_MINUTE)).to.deep.equal([429, 429]);
  });

  it('never counts a signed-in caller\'s refusals (an id it may not open is a 404, however many)', async () => {
    const seen = await statuses('/not-found', SESSION_AUTH_FAILURES_PER_MINUTE + 2);
    expect(new Set(seen)).to.deep.equal(new Set([404]));
  });
});
