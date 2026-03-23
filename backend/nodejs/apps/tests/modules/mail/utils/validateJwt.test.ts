import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { isJwtTokenValid } from '../../../../src/modules/mail/utils/validateJwt'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'

describe('mail/utils/validateJwt', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('isJwtTokenValid', () => {
    const secret = 'test-secret'

    it('should return decoded data for valid token', () => {
      const token = jwt.sign({ userId: 'user-1' }, secret)
      const req: any = { header: sinon.stub().returns(`Bearer ${token}`) }

      const result = isJwtTokenValid(req, secret)

      expect(result).to.have.property('userId', 'user-1')
    })

    it('should throw NotFoundError when authorization header is missing', () => {
      const req: any = { header: sinon.stub().returns(undefined) }

      expect(() => isJwtTokenValid(req, secret)).to.throw(NotFoundError, 'Authorization header not found')
    })

    it('should throw NotFoundError when token is not in Bearer format', () => {
      const req: any = { header: sinon.stub().returns('NoBearer') }

      expect(() => isJwtTokenValid(req, secret)).to.throw(NotFoundError, 'Token not found')
    })

    it('should throw for invalid/expired token', () => {
      const expiredToken = jwt.sign({ userId: 'user-1' }, secret, { expiresIn: '-1s' })
      const req: any = { header: sinon.stub().returns(`Bearer ${expiredToken}`) }

      expect(() => isJwtTokenValid(req, secret)).to.throw()
    })

    it('should throw for token signed with wrong secret', () => {
      const token = jwt.sign({ userId: 'user-1' }, 'wrong-secret')
      const req: any = { header: sinon.stub().returns(`Bearer ${token}`) }

      expect(() => isJwtTokenValid(req, secret)).to.throw()
    })
  })
})
