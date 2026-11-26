import { expect } from 'chai';
import { MongoNetworkError, MongoServerError, MongoServerSelectionError } from 'mongodb';
import { ServiceUnavailableError } from '../../../src/libs/errors/http.errors';
import {
  STORE_UNAVAILABLE_MESSAGE,
  storeTransient,
} from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { transportError as publicError } from '../../../src/modules/agent-harness/transport/errors';
import { transportError as internalError } from '../../../src/modules/agent-harness/transport/internal-controller';

/**
 * CH-06: a Mongo failover under a commit, claim, renew or admission is a retryable 503, never 500 INTERNAL_ERROR.
 * Before, the driver's errors fell through to the unknown-error handler: the worker retried only because it treats
 * every 5xx as an outage, the chat's admission saw an internal error, and the log said "Unhandled error".
 */

const labelled = (label: string): MongoServerError => {
  const error = new MongoServerError({ message: 'interrupted by an election', code: 251 });
  error.addErrorLabel(label);
  return error;
};

const transient = (): Error[] => [
  new MongoNetworkError('connection closed'),
  new MongoServerSelectionError('no primary', { type: 'ReplicaSetNoPrimary' } as never),
  new MongoServerError({ message: 'not primary', code: 10107 }),
  labelled('TransientTransactionError'),
  labelled('RetryableWriteError'),
  Object.assign(new Error('commit failed'), { cause: labelled('TransientTransactionError') }),
];

describe('CH-06: a transient store error is a retryable 503 on every harness route', () => {
  it('recognises a failover, a lost connection and a driver-labelled transient error', () => {
    for (const error of transient()) expect(storeTransient(error), error.message).to.equal(true);
  });

  it('leaves a real refusal alone: a duplicate key or a bad query is not an outage', () => {
    for (const error of [
      new MongoServerError({ message: 'E11000 duplicate key', code: 11000 }),
      new MongoServerError({ message: 'bad query', code: 2 }),
      new Error('something else'),
    ]) {
      expect(storeTransient(error), error.message).to.equal(false);
      expect(publicError(error)).to.equal(error);
      expect(internalError(error)).to.equal(error);
    }
  });

  it('answers 503 on the public and the worker routes, without the driver message', () => {
    for (const error of transient()) {
      for (const mapped of [publicError(error), internalError(error)]) {
        expect(mapped, error.message).to.be.instanceOf(ServiceUnavailableError);
        expect((mapped as ServiceUnavailableError).statusCode).to.equal(503);
        expect((mapped as Error).message).to.equal(STORE_UNAVAILABLE_MESSAGE);
      }
    }
  });
});
