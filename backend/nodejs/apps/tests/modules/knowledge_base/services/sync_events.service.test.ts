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
