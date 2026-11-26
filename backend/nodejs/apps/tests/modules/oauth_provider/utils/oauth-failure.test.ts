import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  answerOAuthServerFailure,
  isOAuthRefusal,
  oauthErrorBody,
  oauthErrorCode,
} from '../../../../src/modules/oauth_provider/utils/oauth-failure'
import {
  AccessDeniedError,
  DeviceGrantError,
  ExpiredTokenError,
  InvalidClientError,
  InvalidGrantError,
  InvalidScopeError,
  InvalidTargetError,
  InvalidTokenError,
  ServerError,
  TokenExchangeError,
  UnauthorizedClientError,
  UnsupportedGrantTypeError,
} from '../../../../src/libs/errors/oauth.errors'
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
  WrongCredentialError,
} from '../../../../src/libs/errors/http.errors'
import { DecryptionError, InvalidKeyFormatError } from '../../../../src/libs/errors/encryption.errors'

describe('oauth-failure', () => {
  describe('isOAuthRefusal', () => {
    it('is true for OAuth errors with a 4xx status and the four deliberate HTTP refusals', () => {
      for (const error of [
        new InvalidGrantError('x'),
        new InvalidClientError('x'),
        new InvalidTokenError('x'),
        new DeviceGrantError('authorization_pending', 'x'),
        new BadRequestError('x'),
        new NotFoundError('x'),
        new UnauthorizedError('x'),
        new WrongCredentialError('x'),
        new ForbiddenError('x'),
      ]) {
        expect(isOAuthRefusal(error), error.constructor.name).to.equal(true)
      }
    })

    // Ours, whatever their status: the client must not be told its request was wrong.
    it('is false for our own errors, 4xx-status ones included', () => {
      for (const error of [
        new DecryptionError('Decryption failed'),
        new InvalidKeyFormatError('bad key'),
        new ConflictError('Another change holds this record; try again'),
        new InternalServerError('x'),
        new ServerError('x'),
        new Error('connection closed'),
        new TypeError('x is undefined'),
        'a string',
        undefined,
      ]) {
        expect(isOAuthRefusal(error), String(error)).to.equal(false)
      }
    })
  })

  describe('oauthErrorCode', () => {
    it('maps each refusal to its RFC 6749 code and never to server_error', () => {
      const table: Array<[Error, string]> = [
        [new InvalidGrantError('x'), 'invalid_grant'],
        [new InvalidTokenError('Refresh token not found'), 'invalid_grant'],
        [new ExpiredTokenError('Refresh token has expired'), 'invalid_grant'],
        [new NotFoundError('x'), 'invalid_grant'],
        [new UnauthorizedError('x'), 'invalid_grant'],
        [new InvalidClientError('x'), 'invalid_client'],
        [new UnauthorizedClientError('x'), 'unauthorized_client'],
        [new UnsupportedGrantTypeError('x'), 'unsupported_grant_type'],
        [new InvalidScopeError('x'), 'invalid_scope'],
        [new InvalidTargetError('x'), 'invalid_target'],
        [new AccessDeniedError('x'), 'access_denied'],
        [new DeviceGrantError('slow_down', 'x'), 'slow_down'],
        [new TokenExchangeError('invalid_target', 'x'), 'invalid_target'],
        [new BadRequestError('x'), 'invalid_request'],
        [new ForbiddenError('x'), 'invalid_request'],
      ]
      for (const [error, code] of table) {
        expect(oauthErrorCode(error as never), error.constructor.name).to.equal(code)
      }
    })
  })

  describe('oauthErrorBody', () => {
    it('carries the refusal message, and the state only when given', () => {
      expect(oauthErrorBody(new InvalidGrantError('code expired'))).to.deep.equal({
        error: 'invalid_grant',
        error_description: 'code expired',
      })
      expect(oauthErrorBody(new InvalidScopeError('x'), 'st1').state).to.equal('st1')
    })
  })

  describe('answerOAuthServerFailure', () => {
    const logger = () => ({ error: sinon.stub(), warn: sinon.stub(), info: sinon.stub() }) as never
    afterEach(() => sinon.restore())

    it('answers 500 server_error, or 503 temporarily_unavailable, with the fixed description', () => {
      for (const [status, code] of [[500, 'server_error'], [503, 'temporarily_unavailable']] as const) {
        const res = { headersSent: false, status: sinon.stub().returnsThis(), json: sinon.stub() }
        answerOAuthServerFailure(logger(), res as never, new Error('db down at 10.0.0.1'), status)
        expect(res.status.calledOnceWith(status)).to.equal(true)
        expect(res.json.firstCall.args[0].error).to.equal(code)
        expect(JSON.stringify(res.json.firstCall.args[0])).to.not.contain('10.0.0.1')
      }
    })

    // A failure after the answer went out (token's telemetry runs after res.json) is logged, never answered twice.
    it('only logs when the answer was already sent', () => {
      const log = logger() as unknown as { error: sinon.SinonStub }
      const res = { headersSent: true, status: sinon.stub().returnsThis(), json: sinon.stub() }
      answerOAuthServerFailure(log as never, res as never, new TypeError('late'))
      expect(res.status.called).to.equal(false)
      expect(log.error.calledOnce).to.equal(true)
    })
  })
})
