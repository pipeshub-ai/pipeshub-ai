import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'http';
import net, { AddressInfo } from 'net';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessInternalApp,
  DEFAULT_INTERNAL_READ_LIMITS,
  INTERNAL_HEADERS_TIMEOUT_ENV,
  INTERNAL_MAX_CONNECTIONS_ENV,
  INTERNAL_REQUEST_TIMEOUT_ENV,
  internalListenerConfig,
} from '../../../src/modules/agent-harness/transport/internal-listener';
import {
  boundedServer,
  HttpReadLimits,
  PUBLIC_READ_LIMITS,
} from '../../../src/modules/agent-harness/transport/read-limits';

/**
 * SEC-DOS-04 (C6.40): the internal listener (and the public one, by its stated
 * bounds) closes a slowloris connection: a head that never completes, or a body
 * that stops arriving. A response that streams after its request is in is never
 * cut.
 */

const TIGHT: HttpReadLimits = {
  headersTimeoutMs: 300,
  requestTimeoutMs: 600,
  keepAliveTimeoutMs: 5_000,
  checkIntervalMs: 50,
  maxConnections: 2,
};

const listen = async (server: http.Server): Promise<number> => {
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return (server.address() as AddressInfo).port;
};

const close = (server: http.Server): Promise<void> =>
  new Promise((resolve) => {
    server.closeAllConnections();
    server.close(() => resolve());
  });

/**
 * What the server sent before it closed the socket after `payload`. Rejects past
 * `HANG_MS`: a bound that was not applied leaves Node's own (a 60 s head, a 5 min
 * request, checked every 30 s), so a slow client would hang far past it.
 */
const HANG_MS = 15_000;
const sentBeforeClose = (port: number, payload: string): Promise<string> =>
  new Promise((resolve, reject) => {
    let received = '';
    const socket = net.connect(port, '127.0.0.1', () => socket.write(payload));
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error(`the connection was still open after ${HANG_MS} ms`));
    }, HANG_MS); // wall-clock-ok: a hang detector; an unapplied bound leaves Node's 30 s check and 60 s head limit
    socket.on('data', (chunk) => {
      received += chunk.toString();
    });
    socket.on('error', () => undefined);
    socket.on('close', () => {
      clearTimeout(timer);
      resolve(received);
    });
  });

/** Node's own answer when a request bound runs out; a socket closed for any other reason gets none. */
const REQUEST_TIMEOUT_ANSWER = /^HTTP\/1\.1 408 /;

const internalApp = (): express.Express => {
  const container = new Container();
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
    verifyWorker: async () => undefined,
    verifyAttemptToken: async () => undefined,
  });
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
  return createAgentHarnessInternalApp(container);
};

/** An Express app as a plain request listener; its own return value is never awaited by the server. */
const listener =
  (app: express.Express): http.RequestListener =>
  (req, res) => {
    app(req, res);
  };

describe('SEC-DOS-04 (C6.40): request-read bounds on the harness listeners', () => {
  it('SEC-DOS-04 C6.40 the internal listener closes a head that never completes within its header bound', async function () {
    this.timeout(HANG_MS + 5_000);
    const server = boundedServer(listener(internalApp()), TIGHT);
    expect(server.headersTimeout).to.equal(TIGHT.headersTimeoutMs);
    const port = await listen(server);
    try {
      const sent = await sentBeforeClose(port, 'POST /api/v1/agent-harness/internal/x HTTP/1.1\r\nHost: x\r\n');
      expect(sent).to.match(REQUEST_TIMEOUT_ANSWER);
    } finally {
      await close(server);
    }
  });

  it('SEC-DOS-04 C6.40 the internal listener closes a body that stops arriving within its request bound', async function () {
    this.timeout(HANG_MS + 5_000);
    const server = boundedServer(listener(internalApp()), TIGHT);
    expect(server.requestTimeout).to.equal(TIGHT.requestTimeoutMs);
    const port = await listen(server);
    try {
      const sent = await sentBeforeClose(
        port,
        // A guarded route (its guard passes here), so its parser waits for the body (SEC-C-09: no app-wide parser).
        'POST /api/v1/agent-harness/internal/runs/claim HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{',
      );
      expect(sent).to.match(REQUEST_TIMEOUT_ANSWER);
    } finally {
      await close(server);
    }
  });

  it('SEC-DOS-04 C6.40 a response streams past every bound once its request is in', async () => {
    const app = express();
    app.get('/stream', (_req, res) => {
      res.setHeader('content-type', 'text/event-stream');
      let sent = 0;
      const tick = setInterval(() => {
        res.write(`data: ${sent}\n\n`);
        sent += 1;
        if (sent === 8) {
          clearInterval(tick);
          res.end();
        }
      }, 150);
    });
    const server = boundedServer(listener(app), TIGHT);
    const port = await listen(server);
    try {
      const started = Date.now();
      const response = await fetch(`http://127.0.0.1:${port}/stream`);
      const body = await response.text();
      expect(body).to.contain('data: 7');
      expect(Date.now() - started).to.be.greaterThan(TIGHT.requestTimeoutMs);
    } finally {
      await close(server);
    }
  });

  it('SEC-DOS-04 C6.40 connections past the cap are dropped', async () => {
    const server = boundedServer(listener(internalApp()), { ...TIGHT, headersTimeoutMs: 5_000, requestTimeoutMs: 5_000 });
    const port = await listen(server);
    let accepted = 0;
    const bothAccepted = new Promise<void>((resolve) => {
      server.on('connection', () => {
        accepted += 1;
        if (accepted === 2) resolve();
      });
    });
    let dropped = 0;
    server.on('drop', () => {
      dropped += 1;
    });
    const held = [net.connect(port, '127.0.0.1'), net.connect(port, '127.0.0.1')];
    try {
      await bothAccepted;
      // Dropped on arrival: closed with nothing sent, where a header bound would have answered 408.
      expect(await sentBeforeClose(port, '')).to.equal('');
      expect(dropped).to.equal(1);
    } finally {
      held.forEach((socket) => socket.destroy());
      await close(server);
    }
  });

  it('SEC-DOS-04 C6.40 the internal listener reads its bounds from the environment, strictly', () => {
    expect(internalListenerConfig({}).readLimits).to.deep.equal(DEFAULT_INTERNAL_READ_LIMITS);
    const configured = internalListenerConfig({
      [INTERNAL_HEADERS_TIMEOUT_ENV]: '5000',
      [INTERNAL_REQUEST_TIMEOUT_ENV]: '20000',
      [INTERNAL_MAX_CONNECTIONS_ENV]: '64',
    }).readLimits;
    expect(configured).to.include({ headersTimeoutMs: 5_000, requestTimeoutMs: 20_000, maxConnections: 64 });
    for (const env of [
      { [INTERNAL_HEADERS_TIMEOUT_ENV]: 'soon' },
      { [INTERNAL_REQUEST_TIMEOUT_ENV]: '0' },
      { [INTERNAL_MAX_CONNECTIONS_ENV]: '1.5' },
      { [INTERNAL_HEADERS_TIMEOUT_ENV]: '90000' },
    ]) {
      expect(() => internalListenerConfig(env)).to.throw(/AGENT_HARNESS_INTERNAL_/);
    }
  });

  it('SEC-DOS-04 C6.40 the public listener bounds a request read, with room for long uploads', () => {
    const server = boundedServer(listener(express()), PUBLIC_READ_LIMITS);
    expect(server.headersTimeout).to.equal(66_000);
    expect(server.requestTimeout).to.equal(300_000);
    expect(server.keepAliveTimeout).to.equal(65_000);
    expect(server.headersTimeout).to.be.greaterThan(server.keepAliveTimeout);
  });
});
