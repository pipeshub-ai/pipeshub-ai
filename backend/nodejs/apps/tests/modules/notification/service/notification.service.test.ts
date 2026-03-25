import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationService } from '../../../../src/modules/notification/service/notification.service'

describe('NotificationService', () => {
  let service: NotificationService
  let mockAuthTokenService: any

  beforeEach(() => {
    mockAuthTokenService = {
      verifyToken: sinon.stub().resolves({ userId: 'user-1', orgId: 'org-1' }),
    }
    service = new NotificationService(mockAuthTokenService)
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

    it('should initialize with empty notification queue', () => {
      expect((service as any).notificationQueue.size).to.equal(0)
    })

    it('should initialize with null io', () => {
      expect((service as any).io).to.be.null
    })

    it('should initialize with null queue processing interval', () => {
      expect((service as any).queueProcessingInterval).to.be.null
    })
  })

  describe('sendToUser', () => {
    it('should return false when io is not initialized', () => {
      const result = service.sendToUser('user-1', 'test-event', { data: 'test' })
      expect(result).to.be.false
    })

    it('should queue notification when user is not connected', () => {
      // Set up io but no connected users
      ;(service as any).io = {
        sockets: { adapter: { rooms: new Map() } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      const result = service.sendToUser('user-1', 'test-event', { data: 'test' })
      expect(result).to.be.false
      // Notification should be queued
      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
      expect(queue[0].event).to.equal('test-event')
    })

    it('should send immediately when user is connected', () => {
      const emitStub = sinon.stub()
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))

      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: emitStub }),
      }

      const result = service.sendToUser('user-1', 'test-event', { data: 'test' })
      expect(result).to.be.true
      expect(emitStub.calledOnce).to.be.true
      expect(emitStub.calledWith('test-event', { data: 'test' })).to.be.true
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

    it('should disconnect all sockets and close io', () => {
      const disconnectSockets = sinon.stub()
      const close = sinon.stub()
      ;(service as any).io = { disconnectSockets, close }

      service.shutdown()
      expect(disconnectSockets.calledWith(true)).to.be.true
      expect(close.calledOnce).to.be.true
      expect((service as any).io).to.be.null
    })

    it('should clear queue processing interval', () => {
      const interval = setInterval(() => {}, 100000)
      ;(service as any).queueProcessingInterval = interval

      service.shutdown()
      expect((service as any).queueProcessingInterval).to.be.null
    })

    it('should clear all queued notifications and connected users', () => {
      ;(service as any).connectedUsers.add('user-1')
      ;(service as any).queueNotification('user-1', 'event', {})
      service.shutdown()
      expect((service as any).connectedUsers.size).to.equal(0)
      expect((service as any).notificationQueue.size).to.equal(0)
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

  describe('queueNotification (private)', () => {
    it('should add notification to queue', () => {
      ;(service as any).queueNotification('user-1', 'event', { msg: 'hello' })
      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
      expect(queue[0].event).to.equal('event')
      expect(queue[0].data).to.deep.equal({ msg: 'hello' })
      expect(queue[0].retryCount).to.equal(0)
      expect(queue[0].userId).to.equal('user-1')
    })

    it('should create new queue for new user', () => {
      ;(service as any).queueNotification('new-user', 'event', {})
      expect((service as any).notificationQueue.has('new-user')).to.be.true
    })

    it('should add timestamp to queued notification', () => {
      const before = Date.now()
      ;(service as any).queueNotification('user-1', 'event', {})
      const after = Date.now()

      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue[0].timestamp).to.be.greaterThanOrEqual(before)
      expect(queue[0].timestamp).to.be.lessThanOrEqual(after)
    })

    it('should drop oldest when queue is full', () => {
      const maxSize = (service as any).MAX_QUEUE_SIZE_PER_USER
      for (let i = 0; i < maxSize + 5; i++) {
        ;(service as any).queueNotification('user-1', `event-${i}`, {})
      }
      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue.length).to.be.at.most(maxSize)
      // The oldest should have been dropped, so the first event should be event-5
      expect(queue[0].event).to.equal('event-5')
    })

    it('should accumulate notifications for the same user', () => {
      ;(service as any).queueNotification('user-1', 'event-1', {})
      ;(service as any).queueNotification('user-1', 'event-2', {})
      ;(service as any).queueNotification('user-1', 'event-3', {})

      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(3)
    })

    it('should maintain separate queues for different users', () => {
      ;(service as any).queueNotification('user-1', 'event-a', {})
      ;(service as any).queueNotification('user-2', 'event-b', {})

      expect((service as any).notificationQueue.get('user-1')).to.have.lengthOf(1)
      expect((service as any).notificationQueue.get('user-2')).to.have.lengthOf(1)
    })
  })

  describe('isUserConnected (private)', () => {
    it('should return false when io is not initialized', () => {
      const result = (service as any).isUserConnected('user-1')
      expect(result).to.be.false
    })

    it('should return false when user has no room', () => {
      ;(service as any).io = {
        sockets: { adapter: { rooms: new Map() } },
      }

      const result = (service as any).isUserConnected('user-1')
      expect(result).to.be.false
    })

    it('should return false when user room is empty', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set())
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
      }

      const result = (service as any).isUserConnected('user-1')
      expect(result).to.be.false
    })

    it('should return true when user room has sockets', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
      }

      const result = (service as any).isUserConnected('user-1')
      expect(result).to.be.true
    })
  })

  describe('sendToUserDirect (private)', () => {
    it('should return false when io is not initialized', () => {
      const result = (service as any).sendToUserDirect('user-1', 'event', {})
      expect(result).to.be.false
    })

    it('should emit event and return true when io is initialized', () => {
      const emitStub = sinon.stub()
      ;(service as any).io = {
        to: sinon.stub().returns({ emit: emitStub }),
      }

      const result = (service as any).sendToUserDirect('user-1', 'event', { data: 'test' })
      expect(result).to.be.true
      expect(emitStub.calledOnce).to.be.true
    })
  })

  describe('processQueuedNotificationsForUser (private)', () => {
    it('should do nothing when queue is empty', () => {
      expect(() => {
        ;(service as any).processQueuedNotificationsForUser('user-1')
      }).to.not.throw()
    })

    it('should do nothing when user not connected', () => {
      ;(service as any).queueNotification('user-1', 'event', {})
      ;(service as any).processQueuedNotificationsForUser('user-1')
      // Queue should remain since user is not connected
      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
    })

    it('should deliver queued notifications when user is connected', () => {
      const emitStub = sinon.stub()
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: emitStub }),
      }

      ;(service as any).queueNotification('user-1', 'event-1', { msg: 'hello' })
      ;(service as any).queueNotification('user-1', 'event-2', { msg: 'world' })
      ;(service as any).processQueuedNotificationsForUser('user-1')

      // Both notifications should have been delivered
      expect(emitStub.callCount).to.equal(2)
      // Queue should be cleared
      expect((service as any).notificationQueue.has('user-1')).to.be.false
    })

    it('should do nothing when queue does not exist for user', () => {
      expect(() => {
        ;(service as any).processQueuedNotificationsForUser('non-existent-user')
      }).to.not.throw()
    })

    it('should handle failed send by incrementing retry count', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))

      // First call to sendToUserDirect returns false (simulating failure)
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      // Override sendToUserDirect to fail
      sinon.stub(service as any, 'sendToUserDirect').returns(false)

      ;(service as any).queueNotification('user-1', 'event', {})
      ;(service as any).processQueuedNotificationsForUser('user-1')

      // Notification should remain in queue with incremented retry count
      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
      expect(queue[0].retryCount).to.equal(1)
    })

    it('should drop notification after max retry count', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      sinon.stub(service as any, 'sendToUserDirect').returns(false)

      // Add notification with retryCount already at max - 1
      const maxRetry = (service as any).MAX_RETRY_COUNT
      ;(service as any).notificationQueue.set('user-1', [{
        userId: 'user-1',
        event: 'event',
        data: {},
        timestamp: Date.now(),
        retryCount: maxRetry - 1,
      }])

      ;(service as any).processQueuedNotificationsForUser('user-1')

      // Notification should be dropped (queue cleared)
      expect((service as any).notificationQueue.has('user-1')).to.be.false
    })
  })

  describe('startQueueProcessing (private)', () => {
    it('should not start duplicate interval', () => {
      ;(service as any).queueProcessingInterval = setInterval(() => {}, 100000)
      const orig = (service as any).queueProcessingInterval
      ;(service as any).startQueueProcessing()
      // Should still be the same interval
      expect((service as any).queueProcessingInterval).to.equal(orig)
      clearInterval(orig)
      ;(service as any).queueProcessingInterval = null
    })

    it('should set up interval when not already running', () => {
      ;(service as any).queueProcessingInterval = null
      ;(service as any).startQueueProcessing()
      expect((service as any).queueProcessingInterval).to.not.be.null
      clearInterval((service as any).queueProcessingInterval)
      ;(service as any).queueProcessingInterval = null
    })
  })

  describe('shutdown with queued notifications', () => {
    it('should clear all queued notifications and connected users', () => {
      ;(service as any).connectedUsers.add('user-1')
      ;(service as any).queueNotification('user-1', 'event', {})
      service.shutdown()
      expect((service as any).connectedUsers.size).to.equal(0)
      expect((service as any).notificationQueue.size).to.equal(0)
    })
  })

  describe('constants', () => {
    it('should have MAX_RETRY_COUNT of 3', () => {
      expect((service as any).MAX_RETRY_COUNT).to.equal(3)
    })

    it('should have QUEUE_PROCESSING_INTERVAL_MS of 5000', () => {
      expect((service as any).QUEUE_PROCESSING_INTERVAL_MS).to.equal(5000)
    })

    it('should have MAX_QUEUE_SIZE_PER_USER of 100', () => {
      expect((service as any).MAX_QUEUE_SIZE_PER_USER).to.equal(100)
    })
  })

  describe('processQueuedNotificationsForUser - error in send', () => {
    it('should handle exception during send and increment retry', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      sinon.stub(service as any, 'sendToUserDirect').throws(new Error('Emit failed'))

      ;(service as any).queueNotification('user-1', 'event', {})
      ;(service as any).processQueuedNotificationsForUser('user-1')

      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
      expect(queue[0].retryCount).to.equal(1)
    })

    it('should drop notification on exception when retry count at max', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      sinon.stub(service as any, 'sendToUserDirect').throws(new Error('Emit failed'))

      const maxRetry = (service as any).MAX_RETRY_COUNT
      ;(service as any).notificationQueue.set('user-1', [{
        userId: 'user-1',
        event: 'event',
        data: {},
        timestamp: Date.now(),
        retryCount: maxRetry - 1,
      }])

      ;(service as any).processQueuedNotificationsForUser('user-1')

      expect((service as any).notificationQueue.has('user-1')).to.be.false
    })

    it('should handle non-Error exception during send', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      sinon.stub(service as any, 'sendToUserDirect').throws('string error')

      ;(service as any).queueNotification('user-1', 'event', {})
      ;(service as any).processQueuedNotificationsForUser('user-1')

      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
      expect(queue[0].retryCount).to.equal(1)
    })

    it('should keep remaining notifications when some succeed and some fail', () => {
      const rooms = new Map()
      rooms.set('user-1', new Set(['socket-1']))
      ;(service as any).io = {
        sockets: { adapter: { rooms } },
        to: sinon.stub().returns({ emit: sinon.stub() }),
      }

      const sendStub = sinon.stub(service as any, 'sendToUserDirect')
      sendStub.onFirstCall().returns(true) // success
      sendStub.onSecondCall().returns(false) // fail

      ;(service as any).queueNotification('user-1', 'event-1', {})
      ;(service as any).queueNotification('user-1', 'event-2', {})
      ;(service as any).processQueuedNotificationsForUser('user-1')

      const queue = (service as any).notificationQueue.get('user-1')
      expect(queue).to.have.lengthOf(1)
      expect(queue[0].event).to.equal('event-2')
    })
  })

  describe('shutdown - with pending notifications count logging', () => {
    it('should log queued notification count during shutdown', () => {
      const disconnectSockets = sinon.stub()
      const close = sinon.stub()
      ;(service as any).io = { disconnectSockets, close }
      ;(service as any).queueNotification('user-1', 'event-1', {})
      ;(service as any).queueNotification('user-1', 'event-2', {})
      ;(service as any).queueNotification('user-2', 'event-3', {})

      service.shutdown()

      expect(disconnectSockets.calledWith(true)).to.be.true
      expect(close.calledOnce).to.be.true
      expect((service as any).notificationQueue.size).to.equal(0)
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
})
