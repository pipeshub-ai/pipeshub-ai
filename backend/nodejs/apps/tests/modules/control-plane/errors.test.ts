/** CPD-1 CE: control-plane refusals take code, status and retryability from the generated catalogue. */
import { expect } from 'chai';
import {
  ControlPlaneRevisionConflictError,
  ControlPlaneUnavailableError,
  ERROR_CODES,
} from '../../../src/modules/control-plane';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { HttpError } from '../../../src/libs/errors/http.errors';

describe('CPD-1 control-plane errors (CE)', () => {
  for (const [error, code] of [
    [new ControlPlaneUnavailableError('down'), 'CP_UNAVAILABLE'],
    [new ControlPlaneRevisionConflictError('moved', '"etag"'), 'CP_REVISION_CONFLICT'],
  ] as const) {
    it(`CE-1: ${code} carries the catalogue's status and retryability, and the routes answer it`, () => {
      expect(error.code).to.equal(code);
      expect(error.status).to.equal(ERROR_CODES[code].status);
      expect(error.retryable).to.equal(ERROR_CODES[code].retryable);
      const http = transportError(error) as HttpError;
      expect(http).to.be.instanceOf(HttpError);
      expect(http.statusCode).to.equal(ERROR_CODES[code].status);
      expect(http.message).to.equal(error.message);
    });
  }
});
