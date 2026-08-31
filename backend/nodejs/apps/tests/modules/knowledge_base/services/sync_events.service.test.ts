import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  SyncEventProducer,
  Event,
  ConnectorSyncEvent,
  BaseSyncEvent,
  ReindexEventPayload,
} from '../../../../src/modules/knowledge_base/services/sync_events.service'

describe('SyncEventProducer', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('Event interface', () => {
    it('should construct a valid Event with ConnectorSyncEvent payload', () => {
      const payload: ConnectorSyncEvent = {
        orgId: 'org-1',
        connector: 'googledrive',
        connectorId: 'conn-1',
        origin: 'google',
        createdAtTimestamp: '123',
        updatedAtTimestamp: '456',
        sourceCreatedAtTimestamp: '789',
      }
      const event: Event = {
        eventType: 'googledrive.sync',
        timestamp: Date.now(),
        payload,
      }
      expect(event.eventType).to.equal('googledrive.sync')
      expect(event.payload.orgId).to.equal('org-1')
    })

    it('should construct a valid Event with ReindexEventPayload', () => {
      const payload: ReindexEventPayload = {
        orgId: 'org-1',
        statusFilters: ['FAILED', 'PENDING'],
      }
      const event: Event = {
        eventType: 'googledrive.reindex',
        timestamp: Date.now(),
        payload,
      }
      expect(event.payload.statusFilters).to.deep.equal(['FAILED', 'PENDING'])
    })

    it('should construct a valid BaseSyncEvent', () => {
      const event: BaseSyncEvent = {
        orgId: 'org-1',
        connector: 'slack',
        connectorId: 'conn-2',
        origin: 'slack',
        fullSync: true,
        createdAtTimestamp: '123',
        updatedAtTimestamp: '456',
        sourceCreatedAtTimestamp: '789',
      }
      expect(event.fullSync).to.be.true
      expect(event.connector).to.equal('slack')
    })

    it('should allow BaseSyncEvent without fullSync', () => {
      const event: BaseSyncEvent = {
        orgId: 'org-1',
        connector: 'jira',
        connectorId: 'conn-3',
        origin: 'jira',
        createdAtTimestamp: '123',
        updatedAtTimestamp: '456',
        sourceCreatedAtTimestamp: '789',
      }
      expect(event.fullSync).to.be.undefined
    })
  })

  describe('SyncEventProducer class', () => {
    it('should be a class', () => {
      expect(SyncEventProducer).to.be.a('function')
    })

    it('should have start method on prototype', () => {
      expect(SyncEventProducer.prototype.start).to.be.a('function')
    })

    it('should have stop method on prototype', () => {
      expect(SyncEventProducer.prototype.stop).to.be.a('function')
    })

    it('should have publishEvent method on prototype', () => {
      expect(SyncEventProducer.prototype.publishEvent).to.be.a('function')
    })
  })
})

describe('SyncEventProducer - coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('interfaces', () => {
    it('should construct ConnectorSyncEvent', () => {
      const event: ConnectorSyncEvent = {
        orgId: 'org-1',
        connector: 'google-drive',
        connectorId: 'conn-1',
        origin: 'google',
        createdAtTimestamp: '123456',
        updatedAtTimestamp: '123456',
        sourceCreatedAtTimestamp: '123456',
      }
      expect(event.connector).to.equal('google-drive')
    })

    it('should construct BaseSyncEvent with optional fullSync', () => {
      const event: BaseSyncEvent = {
        orgId: 'org-1',
        connector: 'slack',
        connectorId: 'conn-2',
        origin: 'slack',
        fullSync: true,
        createdAtTimestamp: '123',
        updatedAtTimestamp: '456',
        sourceCreatedAtTimestamp: '789',
      }
      expect(event.fullSync).to.be.true
    })

    it('should construct ReindexEventPayload', () => {
      const payload: ReindexEventPayload = {
        orgId: 'org-1',
        statusFilters: ['pending', 'failed'],
      }
      expect(payload.statusFilters).to.have.length(2)
    })
  })

  describe('start', () => {
    it('should be callable', async () => {
      const instance = Object.create(SyncEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(false),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.start()
      expect(mockProducer.connect.calledOnce).to.be.true
    })
  })

  describe('stop', () => {
    it('should call disconnect when connected', async () => {
      const instance = Object.create(SyncEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.stop()
      expect(mockProducer.disconnect.calledOnce).to.be.true
    })

    it('should not call disconnect when not connected', async () => {
      const instance = Object.create(SyncEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(false),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.stop()
      expect(mockProducer.disconnect.called).to.be.false
    })
  })

  describe('publishEvent', () => {
    it('should publish event to sync-events topic', async () => {
      const instance = Object.create(SyncEventProducer.prototype)
      ;(instance as any).syncTopic = 'sync-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const event: Event = {
        eventType: 'connectorSync',
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          connector: 'google-drive',
          connectorId: 'conn-1',
          origin: 'google',
          createdAtTimestamp: '123',
          updatedAtTimestamp: '456',
          sourceCreatedAtTimestamp: '789',
        } as ConnectorSyncEvent,
      }

      await instance.publishEvent(event)

      expect(mockProducer.publish.calledOnce).to.be.true
      const [topic, message] = mockProducer.publish.firstCall.args
      expect(topic).to.equal('sync-events')
      expect(message.key).to.equal('connectorSync')
      expect(JSON.parse(message.value)).to.deep.include({ eventType: 'connectorSync' })
      expect(message.headers.eventType).to.equal('connectorSync')
      expect(instance.logger.info.calledOnce).to.be.true
    })

    it('should log error when publish fails', async () => {
      const instance = Object.create(SyncEventProducer.prototype)
      ;(instance as any).syncTopic = 'sync-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().rejects(new Error('Kafka down')),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const event: Event = {
        eventType: 'reindex',
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          statusFilters: ['pending'],
        } as ReindexEventPayload,
      }

      await instance.publishEvent(event)
      expect(instance.logger.error.calledOnce).to.be.true
    })

    it('should include timestamp header as string', async () => {
      const instance = Object.create(SyncEventProducer.prototype)
      ;(instance as any).syncTopic = 'sync-events'
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const timestamp = 9876543210
      const event: Event = {
        eventType: 'syncEvent',
        timestamp,
        payload: { orgId: 'org-1' },
      }

      await instance.publishEvent(event)

      const message = mockProducer.publish.firstCall.args[1]
      expect(message.headers.timestamp).to.equal('9876543210')
    })
  })

  describe('partitioning', () => {
    /**
     * Kafka hashes the message key to choose a partition and hands each
     * partition to exactly one consumer in the group. Keying by eventType put
     * every connector of a given kind on one partition, and therefore on one
     * sync executor — so adding workers bought nothing. Redis Streams is a
     * shared queue and hides this entirely, which is why it went unnoticed.
     */
    const publish = async (event: any) => {
      const producer: any = {
        publish: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      const logger: any = { info: sinon.stub(), error: sinon.stub() }
      const svc = new SyncEventProducer(producer, logger)
      await svc.publishEvent(event)
      return producer.publish.firstCall?.args ?? []
    }

    it('keys by connector so connectors spread across partitions', async () => {
      const [topic, message] = await publish({
        eventType: 'confluence.resync',
        timestamp: 1,
        payload: { connectorId: 'conn-abc', orgId: 'o1' },
      })
      expect(topic).to.equal('sync-events')
      expect(message.key).to.equal('conn-abc')
    })

    it('gives two connectors of the same kind different keys', async () => {
      const [, a] = await publish({
        eventType: 'confluence.resync',
        timestamp: 1,
        payload: { connectorId: 'conn-1' },
      })
      const [, b] = await publish({
        eventType: 'confluence.resync',
        timestamp: 1,
        payload: { connectorId: 'conn-2' },
      })
      expect(a.key).to.not.equal(b.key)
    })

    it('falls back to the event type when there is no connector', async () => {
      const [, message] = await publish({
        eventType: 'sync.all',
        timestamp: 1,
        payload: {},
      })
      expect(message.key).to.equal('sync.all')
    })

    it('does not let a publish failure escape', async () => {
      const producer: any = {
        publish: sinon.stub().rejects(new Error('broker down')),
        isConnected: sinon.stub().returns(true),
      }
      const logger: any = { info: sinon.stub(), error: sinon.stub() }
      const svc = new SyncEventProducer(producer, logger)
      await svc.publishEvent({
        eventType: 'confluence.resync',
        timestamp: 1,
        payload: { connectorId: 'c1' },
      } as any)
      expect(logger.error.called).to.be.true
    })
  })
})
