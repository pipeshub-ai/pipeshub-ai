/**
 * API1-P8 H1: a child run Node refuses answers its catalogue status and code on
 * POST /internal/runs/:runId/children, not 500. The worker reads a 500 as an
 * outage and retried the refused spawn for as long as the run lived; a
 * catalogue refusal it reads as a refused call (CTL-6), and the run goes on.
 * Real internal router (attempt-token guard, validation, child controller) and
 * the app's ErrorMiddleware over a socket; the authority and
 * ChildRunService.spawn are faked at the controller's clients.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_EXECUTION_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  HarnessConflictError,
  HarnessEngineProfileRefusedError,
  HarnessGroupAccessRefusedError,
  HarnessModelAccessRefusedError,
  HarnessUnauthorizedError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';

const RUN = 'run-parent-1';
const CLAIMS = { orgId: 'org-1', runId: RUN, attemptId: 'att-1', fence: 3, workerId: 'w-1' };

type Answer = { status: number; body: any };

describe('API1-P8 H1: POST /internal/runs/:runId/children relays a refusal at its catalogue status', function () {
  this.timeout(10_000);
  let server: Server;
  let base: string;
  let spawn: () => Promise<unknown>;
  let originalLogger: unknown;
  let log: { error: sinon.SinonStub; warn: sinon.SinonStub; info: sinon.SinonStub; debug: sinon.SinonStub };

  beforeEach(async () => {
    spawn = async () => ({ childRunId: 'child-1', created: true });
    log = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = log;
    const container = new Container();
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({
      verifyAttemptToken: async () => CLAIMS,
      authorizeAttempt: async () => ({}),
      credentialFor: async () => null,
    });
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_CHILD_RUNS).toConstantValue({ spawn: () => spawn() });
    const app = express();
    app.use('/internal', createAgentHarnessInternalRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/internal`;
  });

  afterEach(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise((resolve) => server.close(resolve));
  });

  const ask = async (): Promise<Answer> => {
    const response = await fetch(`${base}/runs/${RUN}/children`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: 'Bearer attempt-token' },
      body: JSON.stringify({ attemptId: CLAIMS.attemptId, fence: CLAIMS.fence, invocationId: 'inv-1', goal: 'summarise' }),
    });
    return { status: response.status, body: await response.json() };
  };

  const refusedWith = (error: Error): void => {
    spawn = async () => {
      throw error;
    };
  };

  /** The request's one failure line. */
  const failureLine = (): { level: string; fields: Record<string, any> } => {
    const lines = (['error', 'warn', 'info', 'debug'] as const).flatMap((level) =>
      log[level].getCalls().filter((c) => c.args[0] === 'http.request.error').map((c) => ({ level, fields: c.args[1] })),
    );
    expect(lines, 'one http.request.error').to.have.length(1);
    return lines[0]!;
  };

  it("a model the actor's groups do not allow is 403 HTTP_AGENT_HARNESS_MODEL_GROUP_REQUIRED, logged at warn as forbidden", async () => {
    refusedWith(new HarnessGroupAccessRefusedError('MODEL_GROUP_REQUIRED', ['gpt-x']));
    const r = await ask();
    expect(r.status, JSON.stringify(r.body)).to.equal(403);
    expect(r.body.error.code).to.equal('HTTP_AGENT_HARNESS_MODEL_GROUP_REQUIRED');
    expect(r.body.error.message).to.include('Models you can use: gpt-x.');
    const line = failureLine();
    expect(line.level).to.equal('warn');
    expect(line.fields).to.include({ status: 403, error_class: 'forbidden' });
    expect(log.error.called, 'a refusal is not an internal error').to.equal(false);
  });

  it('a vendor rule refusing the model is 403 HTTP_AGENT_HARNESS_VENDOR_PROHIBITED with its own sentence', async () => {
    refusedWith(new HarnessModelAccessRefusedError('vendor_prohibited', 'This provider does not allow this use', 'row-7'));
    const r = await ask();
    expect(r.status, JSON.stringify(r.body)).to.equal(403);
    expect(r.body.error.code).to.equal('HTTP_AGENT_HARNESS_VENDOR_PROHIBITED');
    expect(r.body.error.message).to.equal('This provider does not allow this use');
  });

  it('vendor rules that do not verify are 503 HTTP_AGENT_HARNESS_VENDOR_RULES_INVALID (not retryable in the catalogue), not 500', async () => {
    refusedWith(new HarnessModelAccessRefusedError('vendor_rules_invalid', 'The vendor rules could not be verified'));
    const r = await ask();
    expect(r.status, JSON.stringify(r.body)).to.equal(503);
    expect(r.body.error.code).to.equal('HTTP_AGENT_HARNESS_VENDOR_RULES_INVALID');
    expect(r.body.error.message).to.equal('The vendor rules could not be verified');
  });

  it('an engine profile that widens its parent is 400 HTTP_AGENT_HARNESS_ENGINE_PROFILE_WIDENING, naming the dimension and layer', async () => {
    const env = process.env.NODE_ENV;
    process.env.NODE_ENV = 'development'; // the error middleware sends metadata only in development
    try {
      refusedWith(new HarnessEngineProfileRefusedError({ code: 'ENGINE_PROFILE_WIDENING', dimension: 'accountKinds', layer: 'request' }));
      const r = await ask();
      expect(r.status, JSON.stringify(r.body)).to.equal(400);
      expect(r.body.error.code).to.equal('HTTP_AGENT_HARNESS_ENGINE_PROFILE_WIDENING');
      expect(r.body.error.metadata).to.deep.equal({ dimension: 'accountKinds', layer: 'request' });
    } finally {
      process.env.NODE_ENV = env;
    }
  });

  it('control: an admitted child is 200 with its run id', async () => {
    const r = await ask();
    expect(r.status).to.equal(200);
    expect(r.body).to.deep.equal({ childRunId: 'child-1', created: true });
  });

  it('control: a child refusal Node already mapped stays 409 with its code', async () => {
    refusedWith(new HarnessConflictError('CHILD_LIMIT_REACHED', 'The run has started all the children it may'));
    const r = await ask();
    expect(r.status).to.equal(409);
    expect(r.body.error.code).to.equal('HTTP_AGENT_HARNESS_CHILD_LIMIT_REACHED');
  });

  it('control: an attempt the authority refuses mid-spawn stays 401 with its reason code', async () => {
    refusedWith(new HarnessUnauthorizedError('superseded'));
    const r = await ask();
    expect(r.status, JSON.stringify(r.body)).to.equal(401);
    expect(r.body.error.code).to.equal('HTTP_AGENT_HARNESS_ATTEMPT_SUPERSEDED');
  });

  it('control: an unavailable dependency stays 503', async () => {
    refusedWith(new HarnessUnavailableError('Which models you may use could not be checked; try again'));
    const r = await ask();
    expect(r.status).to.equal(503);
  });

  it('control: a bug stays 500 INTERNAL_ERROR with the fixed sentence', async () => {
    refusedWith(new TypeError("Cannot read properties of undefined (reading 'kind')"));
    const r = await ask();
    expect(r.status).to.equal(500);
    expect(r.body.error.code).to.equal('INTERNAL_ERROR');
    expect(r.body.error.message).to.not.include('kind');
    expect(failureLine().level).to.equal('error');
  });
});
