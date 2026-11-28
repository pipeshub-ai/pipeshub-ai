import { expect } from 'chai';
import sinon from 'sinon';
import { MongoNetworkError } from 'mongodb';
import {
  ExpectedRefusalError,
  HttpError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../src/libs/errors/http.errors';
import {
  HarnessConflictError,
  HarnessEngineProfileRefusedError,
  HarnessForbiddenError,
  HarnessGroupAccessRefusedError,
  HarnessMemoryRefusedError,
  HarnessModelAccessRefusedError,
  HarnessModelAccountRefusedError,
  HarnessNotFoundError,
  HarnessQuotaExceededError,
  HarnessRunConfigUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { dependencyHealth } from '../../../src/modules/agent-harness/ops/dependency-health';
import { transportError as publicError } from '../../../src/modules/agent-harness/transport/errors';
import { transportError as workerError } from '../../../src/modules/agent-harness/transport/internal-controller';

/**
 * API1-P8 H1: the worker routes answer a refusal they share with the public routes (a child's model access, groups,
 * engine profile) as the public routes do, at its catalogue status; before, it fell through to 500 and the worker
 * retried a spawn that could never be admitted. The worker's own answers come first and stay as they were.
 */

/** What reaches the error middleware: the class, status, code, sentence and metadata. */
function answer(error: unknown): Record<string, unknown> {
  expect(error, String(error)).to.be.instanceOf(HttpError);
  const http = error as HttpError;
  return {
    type: http.constructor.name,
    statusCode: http.statusCode,
    code: http.code,
    message: http.message,
    metadata: http.metadata,
  };
}

describe('API1-P8 H1: the worker-route error mapper falls back to the public catalogue mapper', () => {
  const shared: Array<[string, () => Error, number, string]> = [
    ['a model the groups do not allow', () => new HarnessGroupAccessRefusedError('MODEL_GROUP_REQUIRED', ['gpt-x']), 403, 'MODEL_GROUP_REQUIRED'],
    ['a vendor rule that prohibits the model', () => new HarnessModelAccessRefusedError('vendor_prohibited', 'Not for this use', 'row-7', 'model'), 403, 'VENDOR_PROHIBITED'],
    ['vendor rules that do not verify', () => new HarnessModelAccessRefusedError('vendor_rules_invalid', 'The vendor rules could not be verified'), 503, 'VENDOR_RULES_INVALID'],
    ['an access policy that cannot be read', () => new HarnessModelAccessRefusedError('access_policy_unavailable', 'Try again in a moment'), 503, 'ACCESS_POLICY_UNAVAILABLE'],
    ['an engine profile wider than its parent', () => new HarnessEngineProfileRefusedError({ code: 'ENGINE_PROFILE_WIDENING', dimension: 'accountKinds', layer: 'request' }), 400, 'ENGINE_PROFILE_WIDENING'],
    ['a model account another person owns', () => new HarnessModelAccountRefusedError('MODEL_ACCOUNT_FORBIDDEN', 'Not yours', 'accountId'), 403, 'MODEL_ACCOUNT_FORBIDDEN'],
    ['a memory write not granted', () => new HarnessMemoryRefusedError('MEMORY_WRITE_NOT_GRANTED', 'not granted'), 403, 'MEMORY_WRITE_NOT_GRANTED'],
  ];

  for (const [what, make, status, code] of shared) {
    it(`answers ${what} as the public routes do: ${String(status)} AGENT_HARNESS_${code}`, () => {
      const worker = answer(workerError(make()));
      expect(worker).to.deep.equal(answer(publicError(make())));
      expect(worker.statusCode).to.equal(status);
      expect(worker.code).to.equal(`HTTP_AGENT_HARNESS_${code}`);
    });
  }

  it('keeps the metadata a refusal carries: the engine profile dimension and layer, the access rule and field', () => {
    expect(
      (workerError(new HarnessEngineProfileRefusedError({ code: 'ENGINE_PROFILE_WIDENING', dimension: 'accountKinds', layer: 'request' })) as HttpError).metadata,
    ).to.deep.equal({ dimension: 'accountKinds', layer: 'request' });
    expect(
      (workerError(new HarnessModelAccessRefusedError('vendor_prohibited', 'Not for this use', 'row-7', 'model')) as HttpError).metadata,
    ).to.deep.equal({ rule: 'row-7', field: 'model' });
  });

  it('answers the refusals without a status of their own as the public routes do (forbidden, quota)', () => {
    expect(answer(workerError(new HarnessForbiddenError()))).to.deep.equal(answer(publicError(new HarnessForbiddenError())));
    const quota = (): Error => new HarnessQuotaExceededError('org', 'active_runs', 5);
    expect(answer(workerError(quota()))).to.deep.equal(answer(publicError(quota())));
  });

  it("keeps the worker's own answers ahead of the public ones", () => {
    const missing = workerError(new HarnessNotFoundError());
    expect(missing).to.be.instanceOf(NotFoundError);
    expect((missing as Error).message).to.equal('Run not found');

    const conflict = workerError(new HarnessConflictError('CHILD_LIMIT_REACHED', 'The run has started all the children it may'));
    expect(conflict, 'a conflict is a routine refusal for the worker').to.be.instanceOf(ExpectedRefusalError);
    expect((conflict as HttpError).code).to.equal('HTTP_AGENT_HARNESS_CHILD_LIMIT_REACHED');

    const config = workerError(new HarnessRunConfigUnavailableError());
    expect(config, 'any unavailability is the plain retryable 503').to.be.instanceOf(ServiceUnavailableError);
    expect((config as HttpError).code).to.not.include('RUN_CONFIG_UNAVAILABLE');
  });

  it('answers a transient store error with the 503 and marks mongo degraded, as the public routes do', () => {
    const report = sinon.spy(dependencyHealth, 'report');
    try {
      const failover = (): Error => new MongoNetworkError('connection 7 to 10.0.0.3:27017 closed');
      const worker = workerError(failover());
      expect(report.args.map((args) => args[0])).to.deep.equal(['mongo']);
      expect(worker).to.be.instanceOf(ServiceUnavailableError);
      expect(answer(worker)).to.deep.equal(answer(publicError(failover())));
    } finally {
      report.restore();
    }
  });

  it('leaves an error neither mapper knows alone, for the error middleware to answer 500', () => {
    const bug = new TypeError("Cannot read properties of undefined (reading 'kind')");
    expect(workerError(bug)).to.equal(bug);
  });
});
