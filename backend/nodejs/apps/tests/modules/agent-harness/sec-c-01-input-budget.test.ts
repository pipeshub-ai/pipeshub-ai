/**
 * SEC-C-01: no anonymous body reaches the pasted-secret scan, every pattern the scan runs is linear, an
 * over-long field is refused before it is scanned, and a scan that runs past its budget stops.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import http from 'node:http';

import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import {
  PASTED_INPUT_RULES,
  REDACT_ONLY_PATTERN_SOURCES,
  SECRET_MASK,
  VENDOR_PATTERN_SOURCES,
} from '../../../src/modules/agent-harness/contracts/vectors/token-patterns';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  findPastedSecrets,
  PastedSecretScanTimeout,
  redactPastedSecrets,
} from '../../../src/modules/agent-harness/domain/pasted-secrets';
import {
  PERSON_TEXT_MAX_CHARS,
  removePastedSecrets,
  SCANS_AFTER_AUTH,
  scanAfterAuthentication,
} from '../../../src/modules/agent-harness/transport/pasted-secret-guard';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../src/modules/enterprise_search/routes/es.routes';
import { createMockLogger } from '../../helpers/mock-logger';
import sinon from 'sinon';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import * as pastedSecrets from '../../../src/modules/agent-harness/domain/pasted-secrets';

// Linearity is proven by structure in Python (test_sec_c_pattern_budget.py), which lints these same strings:
// Node's generated vector must be byte for byte the contract's, or that proof does not cover what Node runs.
describe('SEC-C-01: Node runs the very pattern sources the structural proof covers', () => {
  it('the generated vector equals the contract file', () => {
    const contract = JSON.parse(
      readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/token-patterns.json'), 'utf8'),
    ) as {
      patterns: Array<{ kind: string; source: string }>;
      redactOnly: Array<{ kind: string; source: string }>;
      pastedInput: { contextual: { trailing: string }; detectors: Array<{ source: string }>; assigned: { source: string } };
    };
    expect([...VENDOR_PATTERN_SOURCES, ...REDACT_ONLY_PATTERN_SOURCES].map((p) => p.source)).to.deep.equal(
      [...contract.patterns, ...contract.redactOnly].map((p) => p.source),
    );
    expect(PASTED_INPUT_RULES.detectors.map((d) => d.source)).to.deep.equal(contract.pastedInput.detectors.map((d) => d.source));
    expect(PASTED_INPUT_RULES.contextual.trailing).to.equal(contract.pastedInput.contextual.trailing);
    expect(PASTED_INPUT_RULES.assigned.source).to.equal(contract.pastedInput.assigned.source);
  });
});

const MEGABYTE_ATTACKS: Record<string, string> = {
  'trailing dots after a keyword': `token ${'.'.repeat(1_000_000)}a`,
  'mixed trailing punctuation': `token ${'.:!?'.repeat(250_000)}a`,
  'jwt starts after hyphens': '-eyJ'.repeat(250_000),
  'url scheme starts after hyphens': '-A'.repeat(500_000),
  'url scheme starts after pluses': 'x+'.repeat(500_000),
  'private key openers with no end': '-----BEGIN PRIVATE KEY-----\n'.repeat(37_000),
  'keyword then a dotted run': `token ${'a.'.repeat(500_000)}`,
};

describe('SEC-C-01: the shapes that were quadratic are scanned and left alone', function () {
  // A regression is minutes, not milliseconds, here; mocha's timeout is the only clock (WALLCLOCK-1).
  this.timeout(20_000);
  for (const [name, text] of Object.entries(MEGABYTE_ATTACKS)) {
    it(`a megabyte of ${name} holds no secret`, () => {
      expect(redactPastedSecrets(text).kinds).to.deep.equal([]);
    });
  }

  it('still finds what the rewritten patterns found', () => {
    const key = '-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----';
    const jwt = 'eyJ' + 'hbGciOiJIUzI1NiJ9' + '.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlLXZhbHVl';
    const url = 'postgres://admin:' + 'Zq8#vR2!kP' + '@db.internal:5432/app';
    for (const text of [`see ${key} done`, `Authorization: ${jwt}`, `dsn=${url}`]) {
      expect(findPastedSecrets(text), text).to.not.be.empty;
    }
    expect(findPastedSecrets(`-----BEGIN PRIVATE KEY-----\noops\n${key}`).map((f) => f.kind)).to.include('private_key');
    const word = 'q7Rm2Tz9Lp4Wv8Kd3Ns6Hb1Yc5Xe8Ja2Ug';
    const redacted = redactPastedSecrets(`token ${word}.!?`);
    expect(redacted.text).to.equal(`token ${SECRET_MASK}.!?`);
  });

  it('stops at its deadline', () => {
    expect(() => findPastedSecrets('token abc', { deadline: performance.now() - 1 })).to.throw(PastedSecretScanTimeout);
  });
});

const serve = async (app: express.Express): Promise<{ base: string; close: () => Promise<void> }> => {
  const server = await new Promise<http.Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const { port } = server.address() as AddressInfo;
  return { base: `http://127.0.0.1:${port}`, close: () => new Promise((resolve) => server.close(() => resolve())) };
};

const realAuthContainer = (): Container => {
  const container = new Container();
  container
    .bind<AuthMiddleware>('AuthMiddleware')
    .toConstantValue(new AuthMiddleware(createMockLogger() as never, new AuthTokenService('jwt-secret', 'scoped-secret')));
  container.bind('AppConfig').toConstantValue({
    aiBackend: 'http://localhost:8000',
    connectorBackend: 'http://localhost:8088',
    jwtSecret: 'jwt-secret',
    scopedJwtSecret: 'scoped-secret',
  } as never);
  container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
  container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
  return container;
};

describe('SEC-C-01: the scan runs only after authentication', () => {
  const routers: Array<[string, (c: Container) => express.Router]> = [
    ['chat sessions', createChatHarnessSessionRouter],
    ['agent sessions', createAgentHarnessSessionRouter],
    ['conversations', createConversationalRouter],
    ['agent conversations', createAgentConversationalRouter],
  ];
  for (const [name, create] of routers) {
    it(`every ${name} route authenticates, then scans, before anything else reads the body`, () => {
      const router = create(realAuthContainer()) as unknown as {
        stack: Array<{ name: string; route?: { path: string; stack: Array<{ handle: Record<symbol, unknown> }> } }>;
      };
      const routes = router.stack.filter((layer) => layer.route !== undefined);
      expect(routes, 'the router has routes').to.not.be.empty;
      const unguarded = routes
        .filter((layer) => layer.route!.stack[0]?.handle[SCANS_AFTER_AUTH] !== true)
        .map((layer) => layer.route!.path);
      expect(unguarded, 'routes whose first handler is not an authenticator that scans').to.deep.equal([]);
      // No router-wide scan is left in front of authentication.
      expect(router.stack.filter((layer) => layer.route === undefined && layer.name !== 'guardPathParam').map((l) => l.name))
        .to.not.include('removePastedSecrets');
    });
  }

  it('an anonymous megabyte of trailing dots is a 401 through the real authenticator, and is never scanned', async () => {
    const scan = sinon.spy(pastedSecrets, 'redactPastedSecrets');
    const app = express();
    app.use(express.json({ limit: '10mb' }));
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(realAuthContainer()));
    app.use(ErrorMiddleware.handleError());
    const { base, close } = await serve(app);
    try {
      const response = await fetch(`${base}/api/v1/conversations/s1/runs`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ query: `token ${'.'.repeat(1_000_000)}a` }),
      });
      expect(response.status).to.equal(401);
      expect(scan.callCount).to.equal(0);
      // The spy sees the guard's own calls: an authenticated body is scanned through it.
      void removePastedSecrets()({ body: { query: 'hello' } } as never, { headersSent: true } as never, () => undefined);
      expect(scan.callCount).to.equal(1);
    } finally {
      scan.restore();
      await close();
    }
  });
});

describe('SEC-C-01: the guard bounds what it scans', () => {
  const run = async (
    guard: express.RequestHandler,
    body: unknown,
  ): Promise<{ status: number; message: string; reached: unknown[] }> => {
    const reached: unknown[] = [];
    const app = express();
    app.use(express.json({ limit: '10mb' }));
    const auth = scanAfterAuthentication(
      {
        authenticate: ((_req: unknown, _res: unknown, next: () => void) => next()) as never,
        scopedTokenValidator: () => (_req, _res, next) => next(),
      },
      guard,
    );
    app.post('/t', auth.authenticate as express.RequestHandler, (req, res) => {
      reached.push(req.body);
      res.json({ ok: true });
    });
    app.use(ErrorMiddleware.handleError());
    const { base, close } = await serve(app);
    try {
      const response = await fetch(`${base}/t`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const text = await response.text();
      return { status: response.status, message: text, reached };
    } finally {
      await close();
    }
  };

  it('refuses a field longer than a chat message before scanning it, and says what to do', async () => {
    const result = await run(removePastedSecrets(), { query: 'a'.repeat(PERSON_TEXT_MAX_CHARS + 1) });
    expect(result.status).to.equal(400);
    expect(result.message).to.include('Shorten it');
    expect(result.reached).to.be.empty;
  });

  it('refuses an over-long nested comment the same way', async () => {
    const result = await run(removePastedSecrets(), { comments: { negative: 'b'.repeat(PERSON_TEXT_MAX_CHARS + 1) } });
    expect(result.status).to.equal(400);
  });

  it('scans a message at the limit', async () => {
    const result = await run(removePastedSecrets(), { query: 'c'.repeat(PERSON_TEXT_MAX_CHARS) });
    expect(result.status).to.equal(200);
  });

  it('a scan past its budget is a plain 503, and the handler never sees the unscanned text', async () => {
    const result = await run(removePastedSecrets(undefined, -1), { query: 'hello' });
    expect(result.status).to.equal(503);
    expect(result.message).to.include('Send it again');
    expect(result.reached).to.be.empty;
  });

  it('an authenticator that refuses stops the scan', async () => {
    let scanned = 0;
    const app = express();
    app.use(express.json());
    const auth = scanAfterAuthentication(
      {
        authenticate: ((_req: unknown, _res: unknown, next: (e?: unknown) => void) => next(new Error('no'))) as never,
        scopedTokenValidator: () => (_req, _res, next) => next(),
      },
      (_req, _res, next) => {
        scanned += 1;
        next();
      },
    );
    app.post('/t', auth.authenticate as express.RequestHandler, (_req, res) => {
      res.json({});
    });
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-argument handler as error middleware
    app.use((_err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
      res.status(401).end();
    });
    const { base, close } = await serve(app);
    try {
      const response = await fetch(`${base}/t`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"query":"x"}' });
      expect(response.status).to.equal(401);
      expect(scanned).to.equal(0);
    } finally {
      await close();
    }
  });
});
