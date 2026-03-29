import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { verifyGoogleWorkspaceToken } from '../../../../src/modules/tokens_manager/utils/verifyToken'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'

describe('tokens_manager/utils/verifyToken', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('verifyGoogleWorkspaceToken', () => {
    it('should pass when decoded email matches request user email', () => {
      const idToken = jwt.sign({ email: 'user@example.com' }, 'secret')
      const req: any = { user: { email: 'user@example.com' } }
      expect(() => verifyGoogleWorkspaceToken(req, idToken)).to.not.throw()
    })

    it('should throw BadRequestError when decoded email does not match user email', () => {
      const idToken = jwt.sign({ email: 'other@example.com' }, 'secret')
      const req: any = { user: { email: 'user@example.com' } }
      expect(() => verifyGoogleWorkspaceToken(req, idToken)).to.throw(BadRequestError)
    })

    it('should throw BadRequestError when id_token has no email', () => {
      const idToken = jwt.sign({ sub: '123' }, 'secret')
      const req: any = { user: { email: 'user@example.com' } }
      expect(() => verifyGoogleWorkspaceToken(req, idToken)).to.throw(BadRequestError)
    })

    it('should throw BadRequestError when req.user is missing', () => {
      const idToken = jwt.sign({ email: 'user@example.com' }, 'secret')
      const req: any = {}
      expect(() => verifyGoogleWorkspaceToken(req, idToken)).to.throw(BadRequestError)
    })

    it('should throw BadRequestError when id_token is invalid', () => {
      const req: any = { user: { email: 'user@example.com' } }
      expect(() => verifyGoogleWorkspaceToken(req, 'invalid-token')).to.throw(BadRequestError)
    })
  })
})
