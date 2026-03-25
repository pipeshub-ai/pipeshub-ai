import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { MailServiceContainer } from '../../../../src/modules/mail/container/mailService.container'

describe('MailServiceContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (MailServiceContainer as any).instance
  })

  afterEach(() => {
    (MailServiceContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all service bindings', async () => {
      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        communicationBackend: 'http://localhost:3002',
      } as any

      const container = await MailServiceContainer.initialize(appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('MailController')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true

      const instance = MailServiceContainer.getInstance()
      expect(instance).to.equal(container)

      ;(MailServiceContainer as any).instance = null
    })

    it('should bind MailController as dynamic value', async () => {
      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        communicationBackend: 'http://localhost:3002',
      } as any

      const container = await MailServiceContainer.initialize(appConfig)

      // MailController should be resolvable
      const mailController = container.get('MailController')
      expect(mailController).to.exist

      ;(MailServiceContainer as any).instance = null
    })
  })

  describe('dispose', () => {
    it('should set instance to null', async () => {
      const mockContainer = {}
      ;(MailServiceContainer as any).instance = mockContainer

      await MailServiceContainer.dispose()

      expect((MailServiceContainer as any).instance).to.be.null
    })

    it('should do nothing when instance is already null', async () => {
      ;(MailServiceContainer as any).instance = null
      await MailServiceContainer.dispose()
      // Should not throw
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      ;(MailServiceContainer as any).instance = null

      expect(() => MailServiceContainer.getInstance()).to.throw(
        'Mail Service container not initialized',
      )
    })

    it('should return the instance when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      ;(MailServiceContainer as any).instance = mockContainer

      const result = MailServiceContainer.getInstance()
      expect(result).to.equal(mockContainer)
    })
  })
})
