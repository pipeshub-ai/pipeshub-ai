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
})
