/**
 * P7.3 worker memory routes: how a refusal reaches the worker. A malformed
 * request is a 400 that names nothing of the body, an authority refusal is
 * logged and answered as such, and a memory refusal keeps its code.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { AgentHarnessMemoryController } from '../../../src/modules/agent-harness/transport/memory-controller';
import {
  HarnessMemoryRefusedError,
  HarnessUnauthorizedError,
} from '../../../src/modules/agent-harness/domain/errors';
import { BaseError } from '../../../src/libs/errors/base.error';

const claims = { orgId: 'org-1', runId: 'run-1', attemptId: 'a-1', fence: 1 };

const request = (body: Record<string, unknown>) =>
  ({
    params: { runId: 'run-1' },
    query: {},
    body,
    method: 'POST',
    baseUrl: '/internal',
    path: '/runs/run-1/memories',
    harnessAttempt: claims,
  }) as never;

const validBody = {
  attemptId: 'a-1',
  fence: 1,
  content: 'Prefers metric units',
  kind: 'preference',
  scope: 'user',
};

const world = () => {
  const logger = {
    warn: sinon.spy(),
    info: sinon.spy(),
    error: sinon.spy(),
    debug: sinon.spy(),
  };
  const memories = { propose: sinon.stub(), recallForRun: sinon.stub() };
  const authority = { authorizeAttempt: sinon.stub().resolves() };
  const controller = new AgentHarnessMemoryController(
    memories as never,
    authority as never,
    logger as never,
  );
  const res = { status: sinon.stub(), json: sinon.spy() };
  res.status.returns(res);
  return { controller, memories, authority, logger, res };
};

const refusalOf = async (
  w: ReturnType<typeof world>,
  body: Record<string, unknown>,
): Promise<BaseError> => {
  const next = sinon.spy();
  await w.controller.propose(request(body), w.res as never, next);
  expect(next.calledOnce).to.equal(true);
  expect(w.res.json.called).to.equal(false);
  return next.firstCall.args[0] as BaseError;
};

describe('agent harness memory controller refusals (P7.3)', () => {
  it('answers a malformed proposal 400 without echoing it, and logs why for the run', async () => {
    const w = world();
    const error = await refusalOf(w, { ...validBody, content: '', extra: 'x' });
    expect(error.statusCode).to.equal(400);
    expect(error.message).to.equal('Invalid memory request');
    expect(w.memories.propose.called).to.equal(false);
    expect(w.logger.warn.firstCall.args[0]).to.equal('memory.refused');
    expect(w.logger.warn.firstCall.args[1]).to.deep.include({
      orgId: 'org-1',
      runId: 'run-1',
      reason: 'invalid_request',
    });
  });

  it('logs an authority refusal and answers it unauthorized, before any memory is written', async () => {
    const w = world();
    w.authority.authorizeAttempt.rejects(new HarnessUnauthorizedError('superseded'));
    const error = await refusalOf(w, validBody);
    expect(error.statusCode).to.equal(401);
    expect(w.memories.propose.called).to.equal(false);
    expect(w.logger.warn.firstCall.args[0]).to.equal('authority.refused');
  });

  it("keeps a memory refusal's code and status", async () => {
    const w = world();
    w.memories.propose.rejects(
      new HarnessMemoryRefusedError('MEMORY_WRITE_NOT_GRANTED', 'not granted'),
    );
    const error = await refusalOf(w, validBody);
    expect(error.statusCode).to.equal(403);
    expect(error.code).to.equal('HTTP_AGENT_HARNESS_MEMORY_WRITE_NOT_GRANTED');
  });

  it('answers a proposal the service accepted with 200', async () => {
    const w = world();
    w.memories.propose.resolves({ memoryId: 'm-1', status: 'proposed' });
    const next = sinon.spy();
    await w.controller.propose(request(validBody), w.res as never, next);
    expect(next.called).to.equal(false);
    expect(w.res.status.firstCall.args[0]).to.equal(200);
    expect(w.res.json.firstCall.args[0]).to.deep.equal({ memoryId: 'm-1', status: 'proposed' });
    expect(w.memories.propose.firstCall.args).to.deep.equal([
      'org-1',
      'run-1',
      { content: 'Prefers metric units', kind: 'preference', scope: 'user' },
    ]);
  });
});
