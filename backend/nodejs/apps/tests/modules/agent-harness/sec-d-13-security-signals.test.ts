/**
 * SEC-D-13 (ADR-OPS-03): the security decisions Node makes are counted where they are made, with labels from fixed
 * sets, so the five attack alerts have series to read.
 *
 * - the internal listener counts every credential and token it refuses, by kind (from the route's guard) and reason,
 *   with the answer unchanged, also when the recorder or the refusal's logger throws;
 * - the API middleware counts each presented bearer by method (session, PAT, service token, OAuth) and outcome, and
 *   not a request that carried none;
 * - the operator controls in force reach the merged route as a gauge by scope (the kill switch);
 * - the approvals a run asks are counted by a bucket of its org's id, never the id, and survive the merged route's
 *   no-tenant rule;
 * - sign-in and API attempts pass the merged route, and Node's other families still do not.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import http from 'http';
import { AddressInfo } from 'net';
import sinon from 'sinon';
import { Container } from 'inversify';
import jwt from 'jsonwebtoken';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import { AGENT_HARNESS_INTERNAL_PREFIX } from '../../../src/modules/agent-harness/transport/internal-routes';
import { createAgentHarnessInternalApp } from '../../../src/modules/agent-harness/transport/internal-listener';
import {
  countTokenRefusals,
  requireWorkerCredential,
  runTokenRoute,
} from '../../../src/modules/agent-harness/transport/internal-auth';
import {
  FORGED_REASONS,
  recordTokenRejection,
  TOKEN_KINDS,
  TOKEN_REJECTED_METRIC,
  tokenRejectionLabels,
} from '../../../src/modules/agent-harness/adapters/telemetry/token-rejection-metrics';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { UnauthorizedError } from '../../../src/libs/errors/http.errors';
import {
  AUTH_ATTEMPTS_METRIC,
  addAuthAttemptMethods,
  recordAuthAttempt,
  refusalOutcome,
  signInMethod,
} from '../../../src/libs/services/telemetry/modules/auth-metrics';
import {
  PAT_TOKEN_PREFIX,
  SERVICE_TOKEN_PREFIX,
} from '../../../src/modules/oauth_provider/constants/constants';
import {
  HarnessLocalMetrics,
  KILL_SWITCH_METRIC,
  NODE_HARNESS_FAMILIES,
  renderKillSwitch,
} from '../../../src/modules/agent-harness/application/harness-local-metrics';
import type { OperatorControl } from '../../../src/modules/agent-harness/domain/operator-controls';
import { OPERATOR_SCOPE_KINDS } from '../../../src/modules/agent-harness/domain/operator-controls';
import {
  orgBucket,
  prometheusChatPathMetrics,
} from '../../../src/modules/agent-harness/adapters/telemetry/chat-path-metrics';
import { keepFamilies, mergeExpositions } from '../../../src/modules/agent-harness/domain/metrics-exposition';
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend';
import { samples, sampleValue } from '../../helpers/metric-samples';

const serve = async (app: express.Express): Promise<{ base: string; close: () => Promise<void> }> => {
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

const harness = (): Container => {
  const c = new Container();
  c.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
    verifyWorker: async (credential: string | undefined) => {
      if (credential !== 'phw_node') throw new HarnessUnauthorizedError('credential', 'worker credential required');
    },
    verifyAttemptToken: async () => {
      throw new HarnessUnauthorizedError('signature', 'Attempt token signature is invalid');
    },
  });
  c.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
  return c;
};

const rejected = (kind: string, reason: string) => sampleValue(TOKEN_REJECTED_METRIC, { kind, reason });

describe('SEC-D-13: security signals Node counts where it decides', () => {
  afterEach(() => sinon.restore());

  describe('token and credential refusals on the internal listener', () => {
    it('a refused worker credential and a forged attempt token are each one rejection of their kind; the 401 is unchanged', async () => {
      const before = {
        machine: await rejected('machine_credential', 'credential'),
        attempt: await rejected('attempt_token', 'signature'),
      };
      const { base, close } = await serve(createAgentHarnessInternalApp(harness()));
      try {
        const claim = await fetch(`${base}${AGENT_HARNESS_INTERNAL_PREFIX}/runs/claim`, {
          method: 'POST',
          headers: { authorization: 'Bearer stolen', 'content-type': 'application/json' },
          body: '{}',
        });
        expect(claim.status).to.equal(401);
        const events = await fetch(`${base}${AGENT_HARNESS_INTERNAL_PREFIX}/runs/r1/events`, {
          headers: { authorization: 'Bearer forged.attempt.token' },
        });
        expect(events.status).to.equal(401);
        const body = JSON.stringify(await events.json());
        expect(body).to.include('AGENT_HARNESS_ATTEMPT_SIGNATURE');
        expect(body, 'the reason travels on a symbol, never in the body').to.not.include('refusalReason');
      } finally {
        await close();
      }
      expect(await rejected('machine_credential', 'credential')).to.equal(before.machine + 1);
      expect(await rejected('attempt_token', 'signature')).to.equal(before.attempt + 1);
    });

    it("a run-token route's anonymous call counts as a gateway token refusal", async () => {
      const app = express();
      app.post('/runs/:runId/egress', ...runTokenRoute((_req, res) => {
        res.status(204).end();
      }));
      app.use(countTokenRefusals(recordTokenRejection));
      app.use(ErrorMiddleware.handleError());
      const before = await rejected('gateway_token', 'malformed');
      const { base, close } = await serve(app);
      try {
        expect((await fetch(`${base}/runs/r1/egress`, { method: 'POST' })).status).to.equal(401);
      } finally {
        await close();
      }
      expect(await rejected('gateway_token', 'malformed')).to.equal(before + 1);
    });

    it('a recorder that throws, or a logger that throws, never changes the 401; the refusal is still counted', async () => {
      const authority = {
        verifyWorker: async () => {
          throw new HarnessUnauthorizedError('credential', 'no');
        },
      };
      const throwingLogger = {
        warn: () => {
          throw new Error('log sink down');
        },
        info: () => undefined,
        error: () => undefined,
        debug: () => undefined,
      };
      const seen: Array<[string, string]> = [];
      const app = express();
      app.post(
        '/claim',
        requireWorkerCredential(authority as never, throwingLogger as never),
        (_req, res) => {
          res.status(200).end();
        },
      );
      app.post(
        '/claim-again',
        requireWorkerCredential(authority as never),
        (_req, res) => {
          res.status(200).end();
        },
      );
      let calls = 0;
      app.use(
        countTokenRefusals((kind, reason) => {
          calls += 1;
          if (calls === 2) throw new Error('registry full');
          seen.push([kind, reason]);
        }),
      );
      app.use(ErrorMiddleware.handleError());
      const { base, close } = await serve(app);
      try {
        expect((await fetch(`${base}/claim`, { method: 'POST' })).status).to.equal(401);
        expect((await fetch(`${base}/claim-again`, { method: 'POST' })).status).to.equal(401);
      } finally {
        await close();
      }
      expect(seen).to.deep.equal([['machine_credential', 'credential']]);
      expect(calls).to.equal(2);
    });

    it('labels outside the fixed sets read as other, and every forged reason of every kind starts at 0', async () => {
      expect(tokenRejectionLabels('machine_credential', 'credential')).to.deep.equal({
        kind: 'machine_credential',
        reason: 'credential',
      });
      expect(tokenRejectionLabels('Bearer abc.def', 'sig"} 1\nx{')).to.deep.equal({ kind: 'other', reason: 'other' });
      recordTokenRejection('phw_secret_token', 'unknown');
      const series = await samples(TOKEN_REJECTED_METRIC);
      expect(JSON.stringify(series)).to.not.include('phw_secret_token');
      for (const kind of TOKEN_KINDS) {
        for (const reason of FORGED_REASONS) {
          expect(series.some((s) => s.labels['kind'] === kind && s.labels['reason'] === reason), `${kind}/${reason}`).to
            .be.true;
        }
      }
    });
  });

  describe('bearer credentials at the API middleware', () => {
    const middleware = () => {
      const tokenService = sinon.createStubInstance(AuthTokenService);
      tokenService.verifyToken.rejects(new UnauthorizedError('Invalid token'));
      const auth = new AuthMiddleware(
        sinon.createStubInstance(Logger) as unknown as Logger,
        tokenService as unknown as AuthTokenService,
        () => null,
      );
      return auth;
    };
    const call = async (auth: AuthMiddleware, authorization?: string) => {
      const next = sinon.stub();
      const req = { headers: authorization === undefined ? {} : { authorization }, path: '/x', baseUrl: '' };
      await auth.authenticate(req as never, {} as never, next);
      return next.firstCall?.args[0] as unknown;
    };
    const attempts = (outcome: string, method: string) => sampleValue(AUTH_ATTEMPTS_METRIC, { outcome, method });

    it('a refused session token, PAT and service token are each one failure of their method; no token is not an attempt', async () => {
      sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'o1' });
      const auth = middleware();
      const before = {
        session: await attempts('failure', 'session'),
        pat: await attempts('failure', 'pat'),
        service: await attempts('failure', 'service_token'),
        all: (await samples(AUTH_ATTEMPTS_METRIC)).reduce((sum, s) => sum + s.value, 0),
      };
      expect(await call(auth, 'Bearer forged')).to.be.instanceOf(UnauthorizedError);
      expect(await call(auth, `Bearer ${PAT_TOKEN_PREFIX}forged`)).to.be.instanceOf(UnauthorizedError);
      expect(await call(auth, `Bearer ${SERVICE_TOKEN_PREFIX}forged`)).to.be.instanceOf(UnauthorizedError);
      expect(await call(auth)).to.be.instanceOf(UnauthorizedError);
      expect(await attempts('failure', 'session')).to.equal(before.session + 1);
      expect(await attempts('failure', 'pat')).to.equal(before.pat + 1);
      expect(await attempts('failure', 'service_token')).to.equal(before.service + 1);
      const all = (await samples(AUTH_ATTEMPTS_METRIC)).reduce((sum, s) => sum + s.value, 0);
      expect(all, 'the request with no credential is not counted').to.equal(before.all + 3);
    });

    it('an accepted token is one success, and an error after acceptance is not a refusal', async () => {
      sinon.stub(jwt, 'decode').returns({ userId: 'u1', orgId: 'o1' });
      const auth = middleware();
      sinon.stub(auth as unknown as { authenticateRegularToken: () => Promise<void> }, 'authenticateRegularToken').resolves();
      const before = { ok: await attempts('success', 'session'), failed: await attempts('failure', 'session') };
      const next = sinon.stub().onFirstCall().throws(new Error('a downstream handler failed'));
      await auth.authenticate({ headers: { authorization: 'Bearer good' }, path: '/x' } as never, {} as never, next);
      expect(await attempts('success', 'session')).to.equal(before.ok + 1);
      expect(await attempts('failure', 'session')).to.equal(before.failed);
    });

    it('methods and outcomes stay in their fixed sets; a check that broke is an error, not a failure', async () => {
      expect(signInMethod('password')).to.equal('password');
      expect(signInMethod('samlSso')).to.equal('saml');
      expect(signInMethod('azureAd')).to.equal('azure_ad');
      expect(signInMethod('__proto__')).to.equal('other');
      expect(signInMethod({ toString: () => 'password' })).to.equal('other');
      expect(refusalOutcome(new UnauthorizedError('x'))).to.equal('failure');
      expect(refusalOutcome(new Error('db down'))).to.equal('error');
      recordAuthAttempt('alice@acme.test' as never, 'pwned' as never);
      const text = JSON.stringify(await samples(AUTH_ATTEMPTS_METRIC));
      expect(text).to.not.include('alice@acme.test');
      expect(text).to.not.include('pwned');
    });

    it("an edition's own sign-in method is counted under its name once added, and only a name can be added", async () => {
      const before = { added: await attempts('failure', 'directory_x'), other: await attempts('failure', 'other') };
      recordAuthAttempt('directory_x', 'failure');
      expect(await attempts('failure', 'other')).to.equal(before.other + 1);
      addAuthAttemptMethods(['directory_x']);
      addAuthAttemptMethods(['directory_x']);
      recordAuthAttempt('directory_x', 'failure');
      expect(await attempts('failure', 'directory_x')).to.equal(before.added + 1);
      for (const name of ['password', 'alice@acme.test', 'Directory', 'x', `d${'x'.repeat(40)}`]) {
        expect(() => addAuthAttemptMethods([name]), name).to.throw(/Not a new sign-in method label/);
      }
      // A batch with one bad name adds none of it.
      expect(() => addAuthAttemptMethods(['directory_y', 'bad name'])).to.throw();
      recordAuthAttempt('directory_y', 'success');
      expect(await attempts('success', 'directory_y')).to.equal(0);
    });
  });

  describe('the kill switch gauge', () => {
    const control = (scope: OperatorControl['scope'], orgId: string | null = 'org-1'): OperatorControl => ({
      id: `${scope.kind}:${orgId ?? '*'}`,
      scope,
      orgId,
      reason: 'security',
      note: null,
      setBy: 'admin-1',
      setAt: 1,
    });

    it('shows every scope, the controls in force on each, and no org or note', () => {
      const text = renderKillSwitch([
        control({ kind: 'instance' }, null),
        control({ kind: 'egress' }, 'org-1'),
        control({ kind: 'egress' }, 'org-2'),
      ]);
      for (const scope of OPERATOR_SCOPE_KINDS) {
        const value = scope === 'instance' ? 1 : scope === 'egress' ? 2 : 0;
        expect(text).to.include(`${KILL_SWITCH_METRIC}{scope="${scope}"} ${value}`);
      }
      expect(text).to.not.include('org-1');
      expect(text).to.include(`# TYPE ${KILL_SWITCH_METRIC} gauge`);
    });

    it('is read at scrape time; a control service that fails is its own scrape_up 0, none bound is no series', async () => {
      const registry = async () => '';
      const on = new HarnessLocalMetrics({ registry, controls: async () => [control({ kind: 'org' })] });
      const failing = new HarnessLocalMetrics({ registry, controls: async () => Promise.reject(new Error('kv down')) });
      const unbound = new HarnessLocalMetrics({ registry, controls: async () => null });
      const scrape = async (m: HarnessLocalMetrics) =>
        (await m.scrapes()).find((s) => s.service === 'operator_controls');
      expect((await scrape(on))?.body).to.include(`${KILL_SWITCH_METRIC}{scope="org"} 1`);
      expect((await scrape(failing))?.body).to.equal(null);
      expect((await scrape(unbound))?.body).to.equal('');
      expect(mergeExpositions([(await scrape(on))!])).to.include(
        `${KILL_SWITCH_METRIC}{scope="org",harness_service="operator_controls"} 1`,
      );
    });
  });

  describe('approval requests by org bucket', () => {
    it('counts by a bounded bucket of the org id, never the id, and only commits that asked', async () => {
      const org = '65f0c0ffee0000000000abcd';
      const bucket = orgBucket(org);
      expect(bucket).to.match(/^[0-9a-f]{2}$/);
      expect(orgBucket(org)).to.equal(bucket);
      const before = await sampleValue('pipeshub_agent_harness_approvals_requested_total', { org_bucket: bucket });
      prometheusChatPathMetrics.approvalsRequested(org, 3);
      prometheusChatPathMetrics.approvalsRequested(org, 0);
      expect(
        await sampleValue('pipeshub_agent_harness_approvals_requested_total', { org_bucket: bucket }),
      ).to.equal(before + 3);
      const text = await metricsBackend.serialize();
      expect(text).to.not.include(org);
    });

    it('256 buckets at most, whatever org ids exist', () => {
      const buckets = new Set(Array.from({ length: 5_000 }, (_, i) => orgBucket(`org-${i}`)));
      expect(buckets.size).to.be.at.most(256);
    });

    it("passes the merged route's no-tenant rule, which still drops an org label", () => {
      const merged = mergeExpositions([
        {
          service: 'node',
          body:
            '# TYPE pipeshub_agent_harness_approvals_requested_total counter\n' +
            'pipeshub_agent_harness_approvals_requested_total{org_bucket="2a"} 4\n' +
            'pipeshub_agent_harness_approvals_requested_total{org="65f0c0ffee"} 4\n',
        },
      ]);
      expect(merged).to.include('org_bucket="2a"');
      expect(merged).to.not.include('65f0c0ffee');
    });
  });

  it("sign-in and API attempts join the merged route; Node's other families still do not", async () => {
    recordAuthAttempt('password', 'failure');
    const kept = keepFamilies(await metricsBackend.serialize(), NODE_HARNESS_FAMILIES);
    expect(kept).to.include(`${AUTH_ATTEMPTS_METRIC}{outcome="failure",method="password"}`);
    expect(kept).to.not.include('pipeshub_http_requests_total');
  });
});
