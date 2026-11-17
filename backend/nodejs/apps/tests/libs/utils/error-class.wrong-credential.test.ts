import 'reflect-metadata'
import { expect } from 'chai'
import jwt from 'jsonwebtoken'
import { classifyError, classifyRequestError, levelFor } from '../../../src/libs/utils/error-class'
import {
  InternalServerError,
  UnauthorizedError,
  WrongCredentialError,
} from '../../../src/libs/errors/http.errors'
import { withCause } from '../../../src/libs/errors/base.error'

const wrong = (message = 'That sign-in code is not right.'): UnauthorizedError =>
  new WrongCredentialError(message)

describe('error-class: a submitted credential is bad_request, an expired session auth_expired (F32)', () => {
  describe('WrongCredentialError', () => {
    it('is still a 401 UnauthorizedError with the same client code', () => {
      const error = wrong()
      expect(error).to.be.instanceOf(UnauthorizedError)
      expect(error.statusCode).to.equal(401)
      expect(error.code).to.equal('HTTP_UNAUTHORIZED')
      expect(error.name).to.equal('WrongCredentialError')
    })

    it('classifies as bad_request on its own and as a cause', () => {
      expect(classifyError(wrong())).to.equal('bad_request')
      expect(classifyError(withCause(new Error('wrapped'), wrong()))).to.equal('bad_request')
    })

    it('classifies a request it answered 401 as bad_request', () => {
      expect(classifyRequestError(wrong(), 401)).to.equal('bad_request')
    })

    it('lets the answered status decide when the error did not set it', () => {
      expect(classifyRequestError(wrong(), 503)).to.equal('unavailable')
      expect(classifyRequestError(wrong(), 200)).to.equal('internal')
    })

    it('stays internal when our own 500 wraps it', () => {
      expect(classifyRequestError(withCause(new InternalServerError('boom'), wrong()), 500)).to.equal('internal')
    })
  })

  describe('[guard] unchanged', () => {
    const expiredCause = (): Error => {
      try {
        jwt.verify(jwt.sign({ sub: 'u' }, 'k', { expiresIn: -10 }), 'k')
      } catch (error) {
        return error as Error
      }
      throw new Error('token did not expire')
    }

    it('an expired session token is auth_expired', () => {
      const error = withCause(new UnauthorizedError('Invalid token'), expiredCause())
      expect(classifyRequestError(error, 401)).to.equal('auth_expired')
    })

    it('a missing token and a plain UnauthorizedError are auth_expired', () => {
      expect(classifyRequestError(new UnauthorizedError('No token provided'), 401)).to.equal('auth_expired')
      expect(classifyError(new UnauthorizedError('Invalid session'))).to.equal('auth_expired')
    })

    it('a 401 with no error (a handler that answered itself) is auth_expired', () => {
      expect(classifyRequestError(undefined, 401)).to.equal('auth_expired')
      expect(classifyError(401)).to.equal('auth_expired')
    })

    it('bad_request logs at the same level as auth_expired', () => {
      expect(levelFor('bad_request')).to.equal(levelFor('auth_expired')).and.to.equal('warn')
    })
  })
})
