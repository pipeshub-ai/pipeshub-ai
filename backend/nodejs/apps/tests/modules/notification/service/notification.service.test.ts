import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationService } from '../../../../src/modules/notification/service/notification.service'
import { createFakeRedisProvider } from '../../../helpers/fake-redis-provider'

describe('NotificationService', () => {
  let service: NotificationService
  let mockAuthTokenService: any

  beforeEach(() => {
    mockAuthTokenService = {
      verifyToken: sinon.stub().resolves({ userId: 'user-1', orgId: 'org-1' }),
    }
    service = new NotificationService(mockAuthTokenService, createFakeRedisProvider())
  })

  afterEach(() => {
    try {
      if ((service as any).io) {
        service.shutdown()
      }
    } catch (_e) { /* ignore shutdown errors in tests */ }
    sinon.restore()
  })

  describe('constructor', () => {
    it('should create instance', () => {
      expect(service).to.be.instanceOf(NotificationService)
    })

    it('should initialize with no connected users', () => {
      expect((service as any).connectedUsers.size).to.equal(0)
    })

    it('should initialize with null io', () => {
      expect((service as any).io).to.be.null
    })
  })

  describe('sendToUser', () => {
    it('should return false when io is not initialized', () => {
      const result = service.sendToUser('user-1', 'test-event', { data: 'test' })
      expect(result).to.be.false
    })

    it('emits to the user with no socket on this replica, since another replica may hold one', () => {
      const emitStub = sinon.stub()
      const toStub = sinon.stub().returns({ emit: emitStub })
      ;(service as any).io = { sockets: { adapter: { rooms: new Map() } }, to: toStub }

      const result = service.sendToUser('user-1', 'test-event', { data: 'test' })

      expect(result).to.be.true
      expect(toStub.calledOnceWith('user-1')).to.be.true
      expect(emitStub.calledOnceWith('test-event', { data: 'test' })).to.be.true
    })
  })

  describe('sendToOrg', () => {
    it('should return false when io is not initialized', () => {
      const result = service.sendToOrg('org-1', 'test-event', { data: 'test' })
      expect(result).to.be.false
    })

    it('should emit to org room when io is initialized', () => {
      const emitStub = sinon.stub()
      ;(service as any).io = {
        to: sinon.stub().returns({ emit: emitStub }),
      }

      const result = service.sendToOrg('org-1', 'test-event', { data: 'test' })
      expect(result).to.be.true
      expect(emitStub.calledOnce).to.be.true
      expect(emitStub.calledWith('test-event', { data: 'test' })).to.be.true
    })

    it('should emit to org:orgId room format', () => {
      const toStub = sinon.stub().returns({ emit: sinon.stub() })
      ;(service as any).io = { to: toStub }

      service.sendToOrg('org-123', 'event', {})
      expect(toStub.calledWith('org:org-123')).to.be.true
    })
  })

  describe('broadcastToAll', () => {
    it('should not throw when io is not initialized', () => {
      expect(() => service.broadcastToAll('test-event', { data: 'test' })).to.not.throw()
    })

    it('should emit to all clients when io is initialized', () => {
      const emitStub = sinon.stub()
      ;(service as any).io = { emit: emitStub }

      service.broadcastToAll('test-event', { data: 'test' })
      expect(emitStub.calledOnce).to.be.true
      expect(emitStub.calledWith('test-event', { data: 'test' })).to.be.true
    })
  })

  describe('shutdown', () => {
    it('should not throw when called without initialization', () => {
      expect(() => service.shutdown()).to.not.throw()
    })

    it('should clear internal state', () => {
      service.shutdown()
      // Should be safe to call multiple times
      expect(() => service.shutdown()).to.not.throw()
    })

    it("should disconnect this replica's sockets and close io", () => {
      const disconnectSockets = sinon.stub()
      const close = sinon.stub()
      ;(service as any).io = { local: { disconnectSockets }, close }

      service.shutdown()
      expect(disconnectSockets.calledWith(true)).to.be.true
      expect(close.calledOnce).to.be.true
      expect((service as any).io).to.be.null
    })

    it('should clear connected users', () => {
      ;(service as any).connectedUsers.add('user-1')
      service.shutdown()
      expect((service as any).connectedUsers.size).to.equal(0)
    })
  })

  describe('extractToken', () => {
    it('should extract token from Bearer format', () => {
      const result = (service as any).extractToken('Bearer mytoken123')
      expect(result).to.equal('mytoken123')
    })

    it('should return null for non-Bearer format', () => {
      const result = (service as any).extractToken('Basic abc123')
      expect(result).to.be.null
    })

    it('should return null for empty token', () => {
      const result = (service as any).extractToken('')
      expect(result).to.be.null
    })

    it('should return null for Bearer without token', () => {
      const result = (service as any).extractToken('Bearer')
      expect(result).to.be.null
    })

    it('should return null for just a single word (no space)', () => {
      const result = (service as any).extractToken('tokenonly')
      expect(result).to.be.null
    })

    it('should handle Bearer with multiple spaces', () => {
      const result = (service as any).extractToken('Bearer token with spaces')
      expect(result).to.equal('token')
    })
  })

  describe('extractToken edge cases', () => {
    it('should return null for undefined/falsy input', () => {
      // extractToken defaults falsy to 'hfgh' which is not 'Bearer ...'
      const result = (service as any).extractToken(undefined)
      expect(result).to.be.null
    })

    it('should return null for null input', () => {
      const result = (service as any).extractToken(null)
      expect(result).to.be.null
    })
  })

  describe('emitForceLogout', () => {
    it('should return false when io is not initialized', () => {
      const result = service.emitForceLogout('user-1')
      expect(result).to.be.false
    })

    it('should return false when userId is empty', () => {
      ;(service as any).io = {
        sockets: { adapter: { rooms: new Map() } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }
      const result = service.emitForceLogout('')
      expect(result).to.be.false
    })

    it('emits with no socket on this replica, since another replica may hold one', () => {
      const emitStub = sinon.stub()
      ;(service as any).io = {
        sockets: { adapter: { rooms: new Map() } },
        to: sinon.stub().returns({ emit: emitStub }),
      }
      const result = service.emitForceLogout('user-1')
      expect(result).to.be.true
      expect(emitStub.calledOnceWith('force_logout', { reason: 'role_changed' })).to.be.true
    })

    it('should emit force_logout and return true when user is connected', () => {
      const emitStub = sinon.stub()
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: emitStub }),
      }

      const result = service.emitForceLogout('user-1', 'role_changed')
      expect(result).to.be.true
      expect(emitStub.calledWith('force_logout', { reason: 'role_changed' })).to.be.true
    })

    it('should use default reason of role_changed', () => {
      const emitStub = sinon.stub()
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: emitStub }),
      }

      service.emitForceLogout('user-1')
      expect(emitStub.calledWith('force_logout', { reason: 'role_changed' })).to.be.true
    })

    it('should accept missing_role reason', () => {
      const emitStub = sinon.stub()
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: emitStub }),
      }

      service.emitForceLogout('user-1', 'missing_role')
      expect(emitStub.calledWith('force_logout', { reason: 'missing_role' })).to.be.true
    })

    it('should accept invalid_token reason', () => {
      const emitStub = sinon.stub()
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: emitStub }),
      }

      service.emitForceLogout('user-1', 'invalid_token')
      expect(emitStub.calledWith('force_logout', { reason: 'invalid_token' })).to.be.true
    })
  })
})
