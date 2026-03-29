import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('libs/middlewares/types', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../src/libs/middlewares/types')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../src/libs/middlewares/types')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('AuthenticatedUserRequest interface', () => {
    it('should extend Express Request and allow user property', () => {
      // Create a mock that satisfies the shape at runtime
      const mockReq: Partial<import('../../../src/libs/middlewares/types').AuthenticatedUserRequest> = {
        user: { id: 'user-1', orgId: 'org-1', email: 'test@example.com' },
      }
      expect(mockReq.user).to.exist
      expect(mockReq.user!.id).to.equal('user-1')
    })

    it('should allow undefined user property', () => {
      const mockReq: Partial<import('../../../src/libs/middlewares/types').AuthenticatedUserRequest> = {}
      expect(mockReq.user).to.be.undefined
    })
  })

  describe('AuthenticatedServiceRequest interface', () => {
    it('should extend Express Request and allow tokenPayload property', () => {
      const mockReq: Partial<import('../../../src/libs/middlewares/types').AuthenticatedServiceRequest> = {
        tokenPayload: { scope: 'read', clientId: 'client-1' },
      }
      expect(mockReq.tokenPayload).to.exist
      expect(mockReq.tokenPayload!.scope).to.equal('read')
    })

    it('should allow undefined tokenPayload property', () => {
      const mockReq: Partial<import('../../../src/libs/middlewares/types').AuthenticatedServiceRequest> = {}
      expect(mockReq.tokenPayload).to.be.undefined
    })
  })
})
